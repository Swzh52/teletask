from flask import Flask, request, redirect, render_template, jsonify, session
import database as db
import os

flask_app = Flask(__name__)
flask_app.secret_key = os.urandom(24)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")


# ======== 认证 ========
def check_auth():
    return request.cookies.get("auth") == ADMIN_PASSWORD


@flask_app.before_request
def require_login():
    open_paths = ("/login", "/do_login", "/do_files_login", "/do_stats_login")
    if any(request.path.startswith(p) for p in open_paths):
        return
    if not check_auth():
        return redirect("/login")


@flask_app.route("/login")
def login():
    return render_template("login.html", err=request.args.get("err"))


@flask_app.route("/do_login", methods=["POST"])
def do_login():
    if request.form.get("pwd") == ADMIN_PASSWORD:
        resp = redirect("/")
        resp.set_cookie("auth", ADMIN_PASSWORD, max_age=86400 * 7)
        return resp
    return redirect("/login?err=1")


# ======== 主页 ========
@flask_app.route("/")
def index():
    return render_template(
        "index.html",
        keywords  = db.get_keywords(),
        schedules = db.get_schedules(),
        stats     = db.get_stats(),
        ban_rules = db.get_auto_ban_rules(),
    )


# ======== 关键词 CRUD ========
@flask_app.route("/kw/add", methods=["POST"])
def kw_add():
    pattern = request.form.get("pattern", "").strip()
    match   = request.form.get("match", "contains")
    mode    = request.form.get("mode", "random")
    replies = _parse_replies(request.form)
    das     = _parse_seconds(request.form, "kw_delete")
    eas     = _parse_seconds(request.form, "kw_expire")
    if pattern and replies:
        db.add_keyword(pattern, match, mode, replies, das, eas)
    return redirect("/")


@flask_app.route("/kw/edit/<int:kid>", methods=["POST"])
def kw_edit(kid):
    pattern = request.form.get("pattern", "").strip()
    match   = request.form.get("match", "contains")
    mode    = request.form.get("mode", "random")
    replies = _parse_replies(request.form)
    das     = _parse_seconds(request.form, "kw_delete")
    eas     = _parse_seconds(request.form, "kw_expire")
    if pattern:
        db.update_keyword(kid, pattern, match, mode, replies, das, eas)
    return redirect("/")


@flask_app.route("/kw/get/<int:kid>")
def kw_get(kid):
    row = db.get_keyword(kid)
    return jsonify(row if row else {})


@flask_app.route("/kw/delete/<int:kid>")
def kw_delete(kid):
    db.delete_keyword(kid)
    return redirect("/")


@flask_app.route("/kw/toggle/<int:kid>")
def kw_toggle(kid):
    db.toggle_keyword(kid)
    return redirect("/")


def _parse_replies(f):
    """解析多条回复，字段格式：reply_type_0, reply_text_0, ..."""
    replies = []
    i = 0
    while True:
        rtype = f.get(f"reply_type_{i}")
        if rtype is None:
            break
        replies.append({
            "reply_type"   : rtype,
            "reply_text"   : f.get(f"reply_text_{i}",    "").strip() or None,
            "reply_file_id": f.get(f"reply_file_id_{i}", "").strip() or None,
            "reply_caption": f.get(f"reply_caption_{i}", "").strip() or None,
        })
        i += 1
    return replies


def _parse_seconds(f, prefix):
    """将「数量+单位」的表单字段转换成秒数，无效则返回 None"""
    try:
        val  = int(f.get(f"{prefix}_value", 0) or 0)
        unit = int(f.get(f"{prefix}_unit",  1) or 1)
        return val * unit if val > 0 else None
    except (ValueError, TypeError):
        return None


# ======== 定时任务 CRUD ========
@flask_app.route("/sc/add", methods=["POST"])
def sc_add():
    db.add_schedule(**_sc_form(request.form))
    _reload()
    return redirect("/")


@flask_app.route("/sc/edit/<int:sid>", methods=["POST"])
def sc_edit(sid):
    db.update_schedule(sid, **_sc_form(request.form))
    _reload()
    return redirect("/")


@flask_app.route("/sc/get/<int:sid>")
def sc_get(sid):
    row = db.get_schedule(sid)
    return jsonify(dict(row) if row else {})


@flask_app.route("/sc/delete/<int:sid>")
def sc_delete(sid):
    db.delete_schedule(sid)
    _reload()
    return redirect("/")


@flask_app.route("/sc/toggle/<int:sid>")
def sc_toggle(sid):
    db.toggle_schedule(sid)
    _reload()
    return redirect("/")


def _sc_form(f):
    once = f.get("once", "0") == "1"
    if once:
        dt   = f.get("run_at", "").strip()
        # datetime-local 输入格式："2025-01-15T09:00" → 补足秒
        cron = (dt + ":00") if len(dt) == 16 else dt
    else:
        cron = f.get("cron", "").strip()
    das = _parse_seconds(f, "sc_delete")
    return dict(
        name                 = f.get("name",        "").strip(),
        chat_id              = f.get("chat_id",     "").strip(),
        cron                 = cron,
        msg_type             = f.get("msg_type",    "text"),
        msg_text             = f.get("msg_text",    "").strip() or None,
        msg_file_id          = f.get("msg_file_id", "").strip() or None,
        msg_caption          = f.get("msg_caption", "").strip() or None,
        once                 = once,
        delete_after_seconds = das,
    )


def _reload():
    try:
        import bot; bot.reload_schedules()
    except Exception:
        pass


# ======== 统计 ========
@flask_app.route("/stats")
def stats_page():
    if not session.get("stats_auth"):
        return render_template("stats_login.html", err=False)
    return render_template(
        "stats.html",
        kw_logs = db.get_keyword_logs(limit=200),
        sc_logs = db.get_schedule_logs(limit=100),
        banned  = db.get_banned_users(),
        stats   = db.get_stats(),
    )


@flask_app.route("/do_stats_login", methods=["POST"])
def do_stats_login():
    if request.form.get("pwd") == ADMIN_PASSWORD:
        session["stats_auth"] = True
        return redirect("/stats")
    return render_template("stats_login.html", err=True)


@flask_app.route("/stats/logout")
def stats_logout():
    session.pop("stats_auth", None)
    return redirect("/stats")


@flask_app.route("/stats/ban/<int:uid>", methods=["POST"])
def stats_ban(uid):
    if not session.get("stats_auth"):
        return redirect("/stats")
    db.ban_user(uid,
                request.form.get("username",   ""),
                request.form.get("first_name", ""),
                request.form.get("reason",     ""))
    return redirect("/stats")


@flask_app.route("/stats/unban/<int:uid>")
def stats_unban(uid):
    if not session.get("stats_auth"):
        return redirect("/stats")
    db.unban_user(uid)
    return redirect("/stats")


# ======== 自动Ban规则 ========
@flask_app.route("/ban_rules/add", methods=["POST"])
def ban_rules_add():
    try:
        tc = int(request.form.get("trigger_count", 10) or 10)
        ws_val  = int(request.form.get("window_value", 5) or 5)
        ws_unit = int(request.form.get("window_unit", 60) or 60)
        db.add_auto_ban_rule(tc, ws_val * ws_unit)
    except (ValueError, TypeError):
        pass
    return redirect("/")


@flask_app.route("/ban_rules/delete/<int:rid>")
def ban_rules_delete(rid):
    db.delete_auto_ban_rule(rid)
    return redirect("/")


@flask_app.route("/ban_rules/toggle/<int:rid>")
def ban_rules_toggle(rid):
    db.toggle_auto_ban_rule(rid)
    return redirect("/")


# ======== 文件库 ========
@flask_app.route("/files")
def files_page():
    if not session.get("files_auth"):
        return render_template("files_login.html", err=False)
    return render_template("files.html", records=db.get_file_records())


@flask_app.route("/do_files_login", methods=["POST"])
def do_files_login():
    if request.form.get("pwd") == ADMIN_PASSWORD:
        session["files_auth"] = True
        return redirect("/files")
    return render_template("files_login.html", err=True)


@flask_app.route("/files/logout")
def files_logout():
    session.pop("files_auth", None)
    return redirect("/files")


@flask_app.route("/files/rename/<int:fid>", methods=["POST"])
def files_rename(fid):
    if not session.get("files_auth"):
        return redirect("/files")
    new_name = request.form.get("file_name", "").strip()
    if new_name:
        db.update_file_name(fid, new_name)
    return redirect("/files")


@flask_app.route("/files/delete/<int:fid>")
def files_delete(fid):
    if not session.get("files_auth"):
        return redirect("/files")
    deleted_file_id = db.soft_delete_file(fid)
    if deleted_file_id:
        usages = db.get_file_ids_in_use(deleted_file_id)
        if usages:
            import logging
            log = logging.getLogger(__name__)
            names = ", ".join(f"[{u['type']}] {u['name']}" for u in usages)
            log.warning(f"⚠️ 已删除文件 {deleted_file_id[:30]} 仍被使用: {names}")
            # 下次 Bot 尝试发送该文件时会自动通知管理员（见 guarded_send）
    return redirect("/files")


@flask_app.route("/files/check_usages/<int:fid>")
def files_check_usages(fid):
    """AJAX：查询文件被哪些规则引用"""
    if not session.get("files_auth"):
        return jsonify({"error": "unauthorized"}), 403
    records = db.get_file_records()
    target  = next((r for r in records if r["id"] == fid), None)
    if not target:
        return jsonify({"usages": [], "file_name": ""})
    usages = db.get_file_ids_in_use(target["file_id"])
    return jsonify({"usages": usages, "file_name": target["file_name"] or ""})


# ======== 调试 ========
@flask_app.route("/debug/routes")
def debug_routes():
    if not check_auth():
        return "unauthorized", 403
    routes = [f"{','.join(r.methods)} {r.rule}" for r in flask_app.url_map.iter_rules()]
    return "<pre>" + "\n".join(sorted(routes)) + "</pre>"
