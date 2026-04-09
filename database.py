import sqlite3, os, json, random
from datetime import datetime

DB = os.path.join(os.path.dirname(__file__), "tgbot.db")

def get_conn():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
        -- 关键词主表（只存匹配规则）
        CREATE TABLE IF NOT EXISTS keywords (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern TEXT    NOT NULL,
            match   TEXT    NOT NULL DEFAULT 'contains',
            mode    TEXT    NOT NULL DEFAULT 'random',
            active  INTEGER NOT NULL DEFAULT 1
        );

        -- 关键词回复子表（一个关键词多条回复）
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
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL DEFAULT '',
            chat_id     TEXT    NOT NULL,
            cron        TEXT    NOT NULL,
            msg_type    TEXT    NOT NULL DEFAULT 'text',
            msg_text    TEXT,
            msg_file_id TEXT,
            msg_caption TEXT,
            once        INTEGER NOT NULL DEFAULT 0,
            active      INTEGER NOT NULL DEFAULT 1
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
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id      TEXT    NOT NULL,
            file_type    TEXT    NOT NULL,
            file_name    TEXT,
            file_size    INTEGER,
            mime_type    TEXT,
            width        INTEGER,
            height       INTEGER,
            duration     INTEGER,
            uploader_id  INTEGER,
            uploader_name TEXT,
            uploader_username TEXT,
            created_at   DATETIME DEFAULT (datetime('now','localtime'))
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
    """)

    # 兼容旧库迁移
    _migrate(conn)
    conn.close()

def _migrate(conn):
    """安全地向旧表追加新列"""
    migrations = [
        ("schedules",    "name",   "TEXT NOT NULL DEFAULT ''"),
        ("schedules",    "once",   "INTEGER NOT NULL DEFAULT 0"),
        ("file_records", "uploader_id",       "INTEGER"),
        ("file_records", "uploader_name",     "TEXT"),
        ("file_records", "uploader_username", "TEXT"),
    ]
    for table, col, definition in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            conn.commit()
        except Exception:
            pass

    # 旧 keywords 表迁移：把旧的回复字段迁移到 keyword_replies 子表
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
                        "INSERT INTO keyword_replies(keyword_id,reply_type,reply_text,reply_file_id,reply_caption,sort_order) "
                        "VALUES(?,?,?,?,?,0)",
                        (row["id"], row.get("reply_type","text"),
                         row.get("reply_text"), row.get("reply_file_id"),
                         row.get("reply_caption"))
                    )
            conn.commit()
            # 删除旧列（SQLite 3.35+ 支持，低版本跳过）
            for col in ["reply_type","reply_text","reply_file_id","reply_caption"]:
                try:
                    conn.execute(f"ALTER TABLE keywords DROP COLUMN {col}")
                except Exception:
                    pass
            conn.commit()
    except Exception:
        pass

    # keywords 表加 mode 列
    try:
        conn.execute("ALTER TABLE keywords ADD COLUMN mode TEXT NOT NULL DEFAULT 'random'")
        conn.commit()
    except Exception:
        pass

# ======== 关键词主表 ========
def get_keywords():
    """返回关键词列表，每条附带其所有回复"""
    conn = get_conn()
    kws = conn.execute("SELECT * FROM keywords ORDER BY id DESC").fetchall()
    result = []
    for kw in kws:
        kw = dict(kw)
        kw["replies"] = [dict(r) for r in conn.execute(
            "SELECT * FROM keyword_replies WHERE keyword_id=? ORDER BY sort_order,id",
            (kw["id"],)
        ).fetchall()]
        result.append(kw)
    conn.close()
    return result

def get_keyword(kid):
    conn = get_conn()
    kw = conn.execute("SELECT * FROM keywords WHERE id=?", (kid,)).fetchone()
    if not kw:
        return None
    kw = dict(kw)
    kw["replies"] = [dict(r) for r in conn.execute(
        "SELECT * FROM keyword_replies WHERE keyword_id=? ORDER BY sort_order,id",
        (kid,)
    ).fetchall()]
    conn.close()
    return kw

def add_keyword(pattern, match, mode, replies):
    """replies: [{"reply_type":..,"reply_text":..,"reply_file_id":..,"reply_caption":..}]"""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO keywords(pattern,match,mode) VALUES(?,?,?)",
        (pattern, match, mode)
    )
    kid = cur.lastrowid
    for i, r in enumerate(replies):
        conn.execute(
            "INSERT INTO keyword_replies(keyword_id,reply_type,reply_text,reply_file_id,reply_caption,sort_order) "
            "VALUES(?,?,?,?,?,?)",
            (kid, r.get("reply_type","text"), r.get("reply_text"),
             r.get("reply_file_id"), r.get("reply_caption"), i)
        )
    conn.commit(); conn.close()

def update_keyword(kid, pattern, match, mode, replies):
    conn = get_conn()
    conn.execute(
        "UPDATE keywords SET pattern=?,match=?,mode=? WHERE id=?",
        (pattern, match, mode, kid)
    )
    conn.execute("DELETE FROM keyword_replies WHERE keyword_id=?", (kid,))
    for i, r in enumerate(replies):
        conn.execute(
            "INSERT INTO keyword_replies(keyword_id,reply_type,reply_text,reply_file_id,reply_caption,sort_order) "
            "VALUES(?,?,?,?,?,?)",
            (kid, r.get("reply_type","text"), r.get("reply_text"),
             r.get("reply_file_id"), r.get("reply_caption"), i)
        )
    conn.commit(); conn.close()

def delete_keyword(kid):
    conn = get_conn()
    conn.execute("DELETE FROM keyword_replies WHERE keyword_id=?", (kid,))
    conn.execute("DELETE FROM keywords WHERE id=?", (kid,))
    conn.commit(); conn.close()

def toggle_keyword(kid):
    conn = get_conn()
    conn.execute("UPDATE keywords SET active=1-active WHERE id=?", (kid,))
    conn.commit(); conn.close()

def get_keyword_replies(kid):
    return [dict(r) for r in get_conn().execute(
        "SELECT * FROM keyword_replies WHERE keyword_id=? ORDER BY sort_order,id", (kid,)
    ).fetchall()]

# ======== 定时任务 ========
def get_schedules():
    return get_conn().execute("SELECT * FROM schedules ORDER BY id DESC").fetchall()

def get_schedule(sid):
    return get_conn().execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()

def add_schedule(name, chat_id, cron, msg_type, msg_text, msg_file_id, msg_caption, once=0):
    conn = get_conn()
    conn.execute(
        "INSERT INTO schedules(name,chat_id,cron,msg_type,msg_text,msg_file_id,msg_caption,once) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (name, chat_id, cron, msg_type, msg_text, msg_file_id, msg_caption, int(once))
    )
    conn.commit(); conn.close()

def update_schedule(sid, name, chat_id, cron, msg_type, msg_text, msg_file_id, msg_caption, once=0):
    conn = get_conn()
    conn.execute(
        "UPDATE schedules SET name=?,chat_id=?,cron=?,msg_type=?,msg_text=?,msg_file_id=?,msg_caption=?,once=? "
        "WHERE id=?",
        (name, chat_id, cron, msg_type, msg_text, msg_file_id, msg_caption, int(once), sid)
    )
    conn.commit(); conn.close()

def delete_schedule(sid):
    conn = get_conn()
    conn.execute("DELETE FROM schedules WHERE id=?", (sid,))
    conn.commit(); conn.close()

def toggle_schedule(sid):
    conn = get_conn()
    conn.execute("UPDATE schedules SET active=1-active WHERE id=?", (sid,))
    conn.commit(); conn.close()

# ======== 定时任务日志 ========
def log_schedule_start(schedule_id, schedule_name):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO schedule_logs(schedule_id,schedule_name,status,started_at) VALUES(?,?,?,?)",
        (schedule_id, schedule_name, "running",
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    lid = cur.lastrowid
    conn.commit(); conn.close()
    return lid

def log_schedule_done(log_id, success=True, error=None):
    conn = get_conn()
    conn.execute(
        "UPDATE schedule_logs SET status=?,finished_at=?,error=? WHERE id=?",
        ("done" if success else "error",
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), error, log_id)
    )
    conn.commit(); conn.close()

def get_schedule_logs(limit=100):
    return get_conn().execute(
        "SELECT * FROM schedule_logs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()

# ======== 关键词触发日志 ========
def log_keyword_trigger(user_id, username, first_name,
                        chat_id, chat_title, chat_type,
                        keyword_id, keyword_pattern):
    conn = get_conn()
    conn.execute(
        "INSERT INTO keyword_logs(user_id,username,first_name,chat_id,chat_title,"
        "chat_type,keyword_id,keyword_pattern) VALUES(?,?,?,?,?,?,?,?)",
        (user_id, username, first_name, chat_id, chat_title,
         chat_type, keyword_id, keyword_pattern)
    )
    conn.commit(); conn.close()

def get_keyword_logs(limit=200):
    return get_conn().execute(
        "SELECT * FROM keyword_logs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()

# ======== 封禁 ========
def ban_user(user_id, username, first_name, reason=""):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO banned_users(user_id,username,first_name,reason) VALUES(?,?,?,?)",
        (user_id, username, first_name, reason)
    )
    conn.commit(); conn.close()

def unban_user(user_id):
    conn = get_conn()
    conn.execute("DELETE FROM banned_users WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()

def is_banned(user_id):
    return get_conn().execute(
        "SELECT 1 FROM banned_users WHERE user_id=?", (user_id,)
    ).fetchone() is not None

def get_banned_users():
    return get_conn().execute(
        "SELECT * FROM banned_users ORDER BY banned_at DESC"
    ).fetchall()

# ======== 文件记录（含上传者） ========
def add_file_record(file_id, file_type, file_name=None, file_size=None,
                    mime_type=None, width=None, height=None, duration=None,
                    uploader_id=None, uploader_name=None, uploader_username=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO file_records(file_id,file_type,file_name,file_size,mime_type,"
        "width,height,duration,uploader_id,uploader_name,uploader_username) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (file_id, file_type, file_name, file_size, mime_type,
         width, height, duration,
         uploader_id, uploader_name, uploader_username)
    )
    conn.commit(); conn.close()

def get_file_records():
    return get_conn().execute(
        "SELECT * FROM file_records ORDER BY id DESC"
    ).fetchall()

# ======== 统计 ========
def get_stats():
    conn = get_conn()
    def count(sql): return conn.execute(sql).fetchone()[0]
    result = dict(
        kw_total    = count("SELECT COUNT(*) FROM keywords"),
        kw_active   = count("SELECT COUNT(*) FROM keywords WHERE active=1"),
        sc_total    = count("SELECT COUNT(*) FROM schedules"),
        sc_active   = count("SELECT COUNT(*) FROM schedules WHERE active=1"),
        sc_running  = count("SELECT COUNT(*) FROM schedule_logs WHERE status='running'"),
        sc_done     = count("SELECT COUNT(*) FROM schedule_logs WHERE status='done'"),
        sc_error    = count("SELECT COUNT(*) FROM schedule_logs WHERE status='error'"),
        kw_triggers = count("SELECT COUNT(*) FROM keyword_logs"),
        banned      = count("SELECT COUNT(*) FROM banned_users"),
        files       = count("SELECT COUNT(*) FROM file_records"),
    )
    conn.close()
    return result

init_db()