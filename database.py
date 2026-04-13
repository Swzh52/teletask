import sqlite3, os, threading
from datetime import datetime, timedelta

DB = os.path.join(os.path.dirname(__file__), "tgbot.db")

# 全局写锁：SQLite 本身只能串行写，加 Python 侧锁避免多线程排队时
# 互相等待超时。读操作不受影响（WAL 下读不阻塞写）。
_write_lock  = threading.Lock()
_wal_enabled = False   # 模块级标记，PRAGMA 只需执行一次

def get_conn():
    global _wal_enabled
    conn = sqlite3.connect(
        DB,
        check_same_thread=False,
        timeout=30,              # 遇到锁时最多等 30 秒，而不是立即报错
        isolation_level=None,    # 自动提交模式（避免隐式事务长时间占锁）
    )
    conn.row_factory = sqlite3.Row
    # WAL 模式是数据库文件级设置，只需开启一次即可持久化到 .db 文件
    if not _wal_enabled:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            _wal_enabled = True
        except Exception:
            pass
    # synchronous 和 busy_timeout 是连接级设置，每个连接都要设
    try:
        conn.execute("PRAGMA synchronous=NORMAL")   # WAL 下推荐值
        conn.execute("PRAGMA busy_timeout=30000")   # 毫秒，双保险
    except Exception:
        pass
    return conn

def init_db():
    conn = get_conn()
    with _write_lock:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS keywords (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern              TEXT    NOT NULL,
            match                TEXT    NOT NULL DEFAULT 'contains',
            mode                 TEXT    NOT NULL DEFAULT 'random',
            active               INTEGER NOT NULL DEFAULT 1,
            delete_after_seconds INTEGER DEFAULT NULL,
            expire_after_seconds INTEGER DEFAULT NULL,
            expire_at            DATETIME DEFAULT NULL,
            start_at             DATETIME DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS keyword_replies (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword_id    INTEGER NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
            reply_type    TEXT    NOT NULL DEFAULT 'text',
            reply_text    TEXT,
            reply_file_id TEXT,
            reply_caption TEXT,
            sort_order    INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS schedules (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            name                 TEXT    NOT NULL DEFAULT '',
            chat_id              TEXT    NOT NULL,
            cron                 TEXT    NOT NULL,
            msg_type             TEXT    NOT NULL DEFAULT 'text',
            msg_text             TEXT,
            msg_file_id          TEXT,
            msg_caption          TEXT,
            once                 INTEGER NOT NULL DEFAULT 0,
            active               INTEGER NOT NULL DEFAULT 1,
            delete_after_seconds INTEGER DEFAULT NULL,
            start_at             DATETIME DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS schedule_logs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id   INTEGER NOT NULL,
            schedule_name TEXT,
            status        TEXT    NOT NULL DEFAULT 'pending',
            started_at    DATETIME,
            finished_at   DATETIME,
            error         TEXT
        );
        CREATE TABLE IF NOT EXISTS file_records (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id           TEXT    NOT NULL,
            file_type         TEXT    NOT NULL,
            file_name         TEXT,
            file_size         INTEGER,
            mime_type         TEXT,
            width             INTEGER,
            height            INTEGER,
            duration          INTEGER,
            uploader_id       INTEGER,
            uploader_name     TEXT,
            uploader_username TEXT,
            deleted           INTEGER NOT NULL DEFAULT 0,
            deleted_at        DATETIME DEFAULT NULL,
            created_at        DATETIME DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS keyword_logs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            username        TEXT,
            first_name      TEXT,
            chat_id         INTEGER NOT NULL,
            chat_title      TEXT,
            chat_type       TEXT,
            keyword_id      INTEGER NOT NULL,
            keyword_pattern TEXT,
            triggered_at    DATETIME DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            banned_at  DATETIME DEFAULT (datetime('now','localtime')),
            reason     TEXT
        );
        CREATE TABLE IF NOT EXISTS auto_ban_rules (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_count  INTEGER NOT NULL DEFAULT 10,
            window_seconds INTEGER NOT NULL DEFAULT 300,
            active         INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS chats (
            chat_id    INTEGER PRIMARY KEY,
            title      TEXT,
            chat_type  TEXT,
            username   TEXT,
            last_seen  DATETIME DEFAULT (datetime('now','localtime')),
            created_at DATETIME DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS keyword_chats (
            keyword_id INTEGER NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
            chat_id    INTEGER NOT NULL,
            PRIMARY KEY (keyword_id, chat_id)
        );
    """)
        _migrate(conn)
    conn.close()

def _migrate(conn):
    migrations = [
        ("schedules",    "name",                 "TEXT NOT NULL DEFAULT ''"),
        ("schedules",    "once",                 "INTEGER NOT NULL DEFAULT 0"),
        ("schedules",    "delete_after_seconds", "INTEGER DEFAULT NULL"),
        ("schedules",    "start_at",             "DATETIME DEFAULT NULL"),
        ("keywords",     "mode",                 "TEXT NOT NULL DEFAULT 'random'"),
        ("keywords",     "delete_after_seconds", "INTEGER DEFAULT NULL"),
        ("keywords",     "expire_after_seconds", "INTEGER DEFAULT NULL"),
        ("keywords",     "expire_at",            "DATETIME DEFAULT NULL"),
        ("keywords",     "start_at",             "DATETIME DEFAULT NULL"),
        ("file_records", "uploader_id",          "INTEGER"),
        ("file_records", "uploader_name",        "TEXT"),
        ("file_records", "uploader_username",    "TEXT"),
        ("file_records", "deleted",              "INTEGER NOT NULL DEFAULT 0"),
        ("file_records", "deleted_at",           "DATETIME DEFAULT NULL"),
    ]
    for table, col, definition in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            conn.commit()
        except Exception:
            pass
    # 旧回复字段迁移
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(keywords)").fetchall()]
        if "reply_type" in cols:
            rows = conn.execute("SELECT * FROM keywords").fetchall()
            for row in rows:
                row = dict(row)
                exists = conn.execute(
                    "SELECT 1 FROM keyword_replies WHERE keyword_id=?", (row["id"],)
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO keyword_replies(keyword_id,reply_type,reply_text,"
                        "reply_file_id,reply_caption,sort_order) VALUES(?,?,?,?,?,0)",
                        (row["id"], row.get("reply_type","text"), row.get("reply_text"),
                         row.get("reply_file_id"), row.get("reply_caption"))
                    )
            conn.commit()
            for col in ["reply_type","reply_text","reply_file_id","reply_caption"]:
                try:
                    conn.execute(f"ALTER TABLE keywords DROP COLUMN {col}")
                except Exception:
                    pass
            conn.commit()
    except Exception:
        pass

# ======== 关键词 ========
def get_keywords():
    conn = get_conn()
    try:
        kws = conn.execute("SELECT * FROM keywords ORDER BY id DESC").fetchall()
        result = []
        for kw in kws:
            kw = dict(kw)
            kw["replies"] = [dict(r) for r in conn.execute(
                "SELECT * FROM keyword_replies WHERE keyword_id=? ORDER BY sort_order,id",
                (kw["id"],)
            ).fetchall()]
            kw["chat_ids"] = [r["chat_id"] for r in conn.execute(
                "SELECT chat_id FROM keyword_chats WHERE keyword_id=?", (kw["id"],)
            ).fetchall()]
            result.append(kw)
        return result
    finally:
        conn.close()

def get_keyword(kid):
    conn = get_conn()
    try:
        kw = conn.execute("SELECT * FROM keywords WHERE id=?", (kid,)).fetchone()
        if not kw:
            return None
        kw = dict(kw)
        kw["replies"] = [dict(r) for r in conn.execute(
            "SELECT * FROM keyword_replies WHERE keyword_id=? ORDER BY sort_order,id", (kid,)
        ).fetchall()]
        kw["chat_ids"] = [r["chat_id"] for r in conn.execute(
            "SELECT chat_id FROM keyword_chats WHERE keyword_id=?", (kid,)
        ).fetchall()]
        return kw
    finally:
        conn.close()

def add_keyword(pattern, match, mode, replies,
                delete_after_seconds=None, expire_after_seconds=None, start_at=None,
                chat_ids=None):
    conn = get_conn()
    try:
        with _write_lock:
            expire_at = None
            if expire_after_seconds:
                expire_at = (datetime.now() + timedelta(seconds=expire_after_seconds)
                             ).strftime("%Y-%m-%d %H:%M:%S")
            cur = conn.execute(
                "INSERT INTO keywords(pattern,match,mode,delete_after_seconds,"
                "expire_after_seconds,expire_at,start_at) VALUES(?,?,?,?,?,?,?)",
                (pattern, match, mode, delete_after_seconds, expire_after_seconds, expire_at, start_at)
            )
            kid = cur.lastrowid
            for i, r in enumerate(replies):
                conn.execute(
                    "INSERT INTO keyword_replies(keyword_id,reply_type,reply_text,"
                    "reply_file_id,reply_caption,sort_order) VALUES(?,?,?,?,?,?)",
                    (kid, r.get("reply_type","text"), r.get("reply_text"),
                     r.get("reply_file_id"), r.get("reply_caption"), i)
                )
            for cid in (chat_ids or []):
                conn.execute(
                    "INSERT OR IGNORE INTO keyword_chats(keyword_id,chat_id) VALUES(?,?)",
                    (kid, int(cid))
                )
            conn.commit()
            return kid
    finally:
        conn.close()

def update_keyword(kid, pattern, match, mode, replies,
                   delete_after_seconds=None, expire_after_seconds=None, start_at=None,
                   chat_ids=None):
    conn = get_conn()
    try:
        with _write_lock:
            if expire_after_seconds == -1:
                conn.execute(
                    "UPDATE keywords SET pattern=?,match=?,mode=?,delete_after_seconds=?,"
                    "expire_after_seconds=NULL,expire_at=NULL,start_at=? WHERE id=?",
                    (pattern, match, mode, delete_after_seconds, start_at, kid)
                )
            elif expire_after_seconds:
                expire_at = (datetime.now() + timedelta(seconds=expire_after_seconds)
                             ).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "UPDATE keywords SET pattern=?,match=?,mode=?,delete_after_seconds=?,"
                    "expire_after_seconds=?,expire_at=?,start_at=? WHERE id=?",
                    (pattern, match, mode, delete_after_seconds,
                     expire_after_seconds, expire_at, start_at, kid)
                )
            else:
                conn.execute(
                    "UPDATE keywords SET pattern=?,match=?,mode=?,delete_after_seconds=?,start_at=? WHERE id=?",
                    (pattern, match, mode, delete_after_seconds, start_at, kid)
                )
            conn.execute("DELETE FROM keyword_replies WHERE keyword_id=?", (kid,))
            for i, r in enumerate(replies):
                conn.execute(
                    "INSERT INTO keyword_replies(keyword_id,reply_type,reply_text,"
                    "reply_file_id,reply_caption,sort_order) VALUES(?,?,?,?,?,?)",
                    (kid, r.get("reply_type","text"), r.get("reply_text"),
                     r.get("reply_file_id"), r.get("reply_caption"), i)
                )
            # chat_ids=None → 不修改关联关系；chat_ids=[] → 清空（即所有群组）
            if chat_ids is not None:
                conn.execute("DELETE FROM keyword_chats WHERE keyword_id=?", (kid,))
                for cid in chat_ids:
                    conn.execute(
                        "INSERT OR IGNORE INTO keyword_chats(keyword_id,chat_id) VALUES(?,?)",
                        (kid, int(cid))
                    )
            conn.commit()
    finally:
        conn.close()

def delete_keyword(kid):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute("DELETE FROM keyword_replies WHERE keyword_id=?", (kid,))
            conn.execute("DELETE FROM keywords WHERE id=?", (kid,))
            conn.commit()
    finally:
        conn.close()

def toggle_keyword(kid):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute("UPDATE keywords SET active=1-active WHERE id=?", (kid,))
            conn.commit()
    finally:
        conn.close()

def deactivate_keyword(kid):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute("UPDATE keywords SET active=0 WHERE id=?", (kid,))
            conn.commit()
    finally:
        conn.close()

def get_expired_keywords():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM keywords WHERE active=1 AND expire_at IS NOT NULL AND expire_at <= ?",
            (now,)
        ).fetchall()]
    finally:
        conn.close()

# ======== 批量操作 ========
def bulk_toggle_keywords(ids: list, active: int):
    if not ids:
        return
    conn = get_conn()
    try:
        with _write_lock:
            ph = ",".join("?" * len(ids))
            conn.execute(f"UPDATE keywords SET active=? WHERE id IN ({ph})", [active] + list(ids))
            conn.commit()
    finally:
        conn.close()

def bulk_delete_keywords(ids: list):
    if not ids:
        return
    conn = get_conn()
    try:
        with _write_lock:
            ph = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM keyword_replies WHERE keyword_id IN ({ph})", list(ids))
            conn.execute(f"DELETE FROM keywords WHERE id IN ({ph})", list(ids))
            conn.commit()
    finally:
        conn.close()

def bulk_toggle_schedules(ids: list, active: int):
    if not ids:
        return
    conn = get_conn()
    try:
        with _write_lock:
            ph = ",".join("?" * len(ids))
            conn.execute(f"UPDATE schedules SET active=? WHERE id IN ({ph})", [active] + list(ids))
            conn.commit()
    finally:
        conn.close()

def bulk_delete_schedules(ids: list):
    if not ids:
        return
    conn = get_conn()
    try:
        with _write_lock:
            ph = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM schedules WHERE id IN ({ph})", list(ids))
            conn.commit()
    finally:
        conn.close()

# ======== 定时任务 ========
def get_schedules():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM schedules ORDER BY id DESC"
        ).fetchall()]
    finally:
        conn.close()

def get_schedule(sid):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def add_schedule(name, chat_id, cron, msg_type, msg_text, msg_file_id, msg_caption,
                 once=0, delete_after_seconds=None, start_at=None):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute(
                "INSERT INTO schedules(name,chat_id,cron,msg_type,msg_text,msg_file_id,"
                "msg_caption,once,delete_after_seconds,start_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (name, chat_id, cron, msg_type, msg_text, msg_file_id, msg_caption,
                 int(once), delete_after_seconds, start_at)
            )
            conn.commit()
    finally:
        conn.close()

def update_schedule(sid, name, chat_id, cron, msg_type, msg_text, msg_file_id,
                    msg_caption, once=0, delete_after_seconds=None, start_at=None):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute(
                "UPDATE schedules SET name=?,chat_id=?,cron=?,msg_type=?,msg_text=?,"
                "msg_file_id=?,msg_caption=?,once=?,delete_after_seconds=?,start_at=? WHERE id=?",
                (name, chat_id, cron, msg_type, msg_text, msg_file_id, msg_caption,
                 int(once), delete_after_seconds, start_at, sid)
            )
            conn.commit()
    finally:
        conn.close()

def delete_schedule(sid):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute("DELETE FROM schedules WHERE id=?", (sid,))
            conn.commit()
    finally:
        conn.close()

def toggle_schedule(sid):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute("UPDATE schedules SET active=1-active WHERE id=?", (sid,))
            conn.commit()
    finally:
        conn.close()

# ======== 定时任务日志 ========
def log_schedule_start(schedule_id, schedule_name):
    conn = get_conn()
    try:
        with _write_lock:
            cur = conn.execute(
                "INSERT INTO schedule_logs(schedule_id,schedule_name,status,started_at) VALUES(?,?,?,?)",
                (schedule_id, schedule_name, "running",
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            lid = cur.lastrowid
            conn.commit()
            return lid
    finally:
        conn.close()

def log_schedule_done(log_id, success=True, error=None):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute(
                "UPDATE schedule_logs SET status=?,finished_at=?,error=? WHERE id=?",
                ("done" if success else "error",
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"), error, log_id)
            )
            conn.commit()
    finally:
        conn.close()

def get_schedule_logs(limit=100):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM schedule_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()]
    finally:
        conn.close()

# ======== 关键词触发日志 ========
def log_keyword_trigger(user_id, username, first_name,
                        chat_id, chat_title, chat_type,
                        keyword_id, keyword_pattern):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute(
                "INSERT INTO keyword_logs(user_id,username,first_name,chat_id,chat_title,"
                "chat_type,keyword_id,keyword_pattern) VALUES(?,?,?,?,?,?,?,?)",
                (user_id, username, first_name, chat_id, chat_title,
                 chat_type, keyword_id, keyword_pattern)
            )
            conn.commit()
    finally:
        conn.close()

def get_keyword_logs(limit=200):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM keyword_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()]
    finally:
        conn.close()

# ======== 封禁 ========
def ban_user(user_id, username, first_name, reason=""):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute(
                "INSERT OR REPLACE INTO banned_users(user_id,username,first_name,reason) VALUES(?,?,?,?)",
                (user_id, username, first_name, reason)
            )
            conn.commit()
    finally:
        conn.close()

def unban_user(user_id):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute("DELETE FROM banned_users WHERE user_id=?", (user_id,))
            conn.commit()
    finally:
        conn.close()

def is_banned(user_id):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT 1 FROM banned_users WHERE user_id=?", (user_id,)
        ).fetchone() is not None
    finally:
        conn.close()

def get_banned_users():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM banned_users ORDER BY banned_at DESC"
        ).fetchall()]
    finally:
        conn.close()

# ======== 自动Ban规则 ========
def get_auto_ban_rules():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM auto_ban_rules ORDER BY id"
        ).fetchall()]
    finally:
        conn.close()

def add_auto_ban_rule(trigger_count, window_seconds):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute(
                "INSERT INTO auto_ban_rules(trigger_count,window_seconds) VALUES(?,?)",
                (trigger_count, window_seconds)
            )
            conn.commit()
    finally:
        conn.close()

def delete_auto_ban_rule(rid):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute("DELETE FROM auto_ban_rules WHERE id=?", (rid,))
            conn.commit()
    finally:
        conn.close()

def toggle_auto_ban_rule(rid):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute("UPDATE auto_ban_rules SET active=1-active WHERE id=?", (rid,))
            conn.commit()
    finally:
        conn.close()

# ======== 文件记录 ========
def add_file_record(file_id, file_type, file_name=None, file_size=None,
                    mime_type=None, width=None, height=None, duration=None,
                    uploader_id=None, uploader_name=None, uploader_username=None):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute(
                "INSERT INTO file_records(file_id,file_type,file_name,file_size,mime_type,"
                "width,height,duration,uploader_id,uploader_name,uploader_username) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (file_id, file_type, file_name, file_size, mime_type,
                 width, height, duration, uploader_id, uploader_name, uploader_username)
            )
            conn.commit()
    finally:
        conn.close()

def get_file_records():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM file_records WHERE deleted=0 ORDER BY id DESC"
        ).fetchall()]
    finally:
        conn.close()

def update_file_name(fid, new_name):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute("UPDATE file_records SET file_name=? WHERE id=?", (new_name, fid))
            conn.commit()
    finally:
        conn.close()

def soft_delete_file(fid):
    conn = get_conn()
    try:
        with _write_lock:
            row = conn.execute("SELECT file_id FROM file_records WHERE id=?", (fid,)).fetchone()
            if not row:
                return None
            file_id = row["file_id"]
            conn.execute(
                "UPDATE file_records SET deleted=1,deleted_at=? WHERE id=?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), fid)
            )
            conn.commit()
            return file_id
    finally:
        conn.close()

def bulk_soft_delete_files(fids):
    """批量软删除文件。返回被删除的 file_id 列表（供调用方检查引用并告警）。"""
    if not fids:
        return []
    conn = get_conn()
    try:
        with _write_lock:
            placeholders = ",".join("?" * len(fids))
            rows = conn.execute(
                f"SELECT file_id FROM file_records WHERE id IN ({placeholders}) AND deleted=0",
                tuple(fids)
            ).fetchall()
            deleted_file_ids = [r["file_id"] for r in rows]
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                f"UPDATE file_records SET deleted=1,deleted_at=? WHERE id IN ({placeholders})",
                (now, *fids)
            )
            conn.commit()
            return deleted_file_ids
    finally:
        conn.close()

def get_file_ids_in_use(file_id):
    """查找引用该 file_id 的所有规则（包括已停用的，否则用户删除文件后
    重新启用规则会静默失败）"""
    conn = get_conn()
    try:
        usages = []
        kw_rows = conn.execute(
            "SELECT k.id, k.pattern, k.active FROM keyword_replies kr "
            "JOIN keywords k ON kr.keyword_id=k.id "
            "WHERE kr.reply_file_id=?", (file_id,)
        ).fetchall()
        for r in kw_rows:
            usages.append({"type": "keyword", "id": r["id"],
                           "name": r["pattern"], "active": r["active"]})
        sc_rows = conn.execute(
            "SELECT id, name, active FROM schedules WHERE msg_file_id=?", (file_id,)
        ).fetchall()
        for r in sc_rows:
            usages.append({"type": "schedule", "id": r["id"],
                           "name": r["name"], "active": r["active"]})
        return usages
    finally:
        conn.close()

def is_file_id_active(file_id):
    if not file_id:
        return True
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT 1 FROM file_records WHERE file_id=? AND deleted=0", (file_id,)
        ).fetchone() is not None
    finally:
        conn.close()

# ======== 群组管理 ========
def upsert_chat(chat_id, title=None, chat_type=None, username=None):
    """Bot 收到消息时被动记录/更新群组信息"""
    if not chat_id:
        return
    conn = get_conn()
    try:
        with _write_lock:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            exists = conn.execute("SELECT 1 FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
            if exists:
                conn.execute(
                    "UPDATE chats SET title=COALESCE(?,title),chat_type=COALESCE(?,chat_type),"
                    "username=COALESCE(?,username),last_seen=? WHERE chat_id=?",
                    (title, chat_type, username, now, chat_id)
                )
            else:
                conn.execute(
                    "INSERT INTO chats(chat_id,title,chat_type,username,last_seen) VALUES(?,?,?,?,?)",
                    (chat_id, title or str(chat_id), chat_type, username, now)
                )
            conn.commit()
    finally:
        conn.close()

def get_chats():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM chats ORDER BY last_seen DESC"
        ).fetchall()]
    finally:
        conn.close()

def add_chat_manual(chat_id, title):
    """管理员手动添加群组"""
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute(
                "INSERT OR REPLACE INTO chats(chat_id,title,chat_type) VALUES(?,?,'manual')",
                (int(chat_id), title or str(chat_id))
            )
            conn.commit()
    finally:
        conn.close()

def update_chat_title(chat_id, title):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute("UPDATE chats SET title=? WHERE chat_id=?", (title, chat_id))
            conn.commit()
    finally:
        conn.close()

def delete_chat(chat_id):
    conn = get_conn()
    try:
        with _write_lock:
            conn.execute("DELETE FROM chats WHERE chat_id=?", (chat_id,))
            conn.execute("DELETE FROM keyword_chats WHERE chat_id=?", (chat_id,))
            conn.commit()
    finally:
        conn.close()


# ======== 统计 ========
def get_stats():
    conn = get_conn()
    try:
        def count(sql): return conn.execute(sql).fetchone()[0]
        return dict(
            kw_total    = count("SELECT COUNT(*) FROM keywords"),
            kw_active   = count("SELECT COUNT(*) FROM keywords WHERE active=1"),
            sc_total    = count("SELECT COUNT(*) FROM schedules"),
            sc_active   = count("SELECT COUNT(*) FROM schedules WHERE active=1"),
            sc_running  = count("SELECT COUNT(*) FROM schedule_logs WHERE status='running'"),
            sc_done     = count("SELECT COUNT(*) FROM schedule_logs WHERE status='done'"),
            sc_error    = count("SELECT COUNT(*) FROM schedule_logs WHERE status='error'"),
            kw_triggers = count("SELECT COUNT(*) FROM keyword_logs"),
            banned      = count("SELECT COUNT(*) FROM banned_users"),
            files       = count("SELECT COUNT(*) FROM file_records WHERE deleted=0"),
        )
    finally:
        conn.close()

init_db()
