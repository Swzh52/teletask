from flask import Flask, request, redirect, render_template, jsonify, session
import database as db
import os

flask_app = Flask(__name__)
flask_app.secret_key = os.urandom(24)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")


# ======== 登录验证 ========
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
        keywords=db.get_keywords(),
        schedules=db.get_schedules(),
        stats=db.get_stats(),
    )


# ======== 关键词 CRUD ========
@flask_app.route("/kw/add", methods=["POST"])
def kw_add():
    pattern = request.form.get("pattern", "").strip()
    match   = request.form.get("match", "contains")
    mode    = request.form.get("mode", "random")
    replies = _parse_replies(request.form)
    if pattern and replies:
        db.add_keyword(pattern, match, mode, replies)
    return redirect("/")


@flask_app.route("/kw/edit/<int:kid>", methods=["POST"])
def kw_edit(kid):
    pattern = request.form.get("pattern", "").strip()
    match   = request.form.get("match", "contains")
    mode    = request.form.get("mode", "random")
    replies = _parse_replies(request.form)
    if pattern:
        db.update_keyword(kid, pattern, match, mode, replies)
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
    """解析表单中的多条回复，字段格式：reply_type_0, reply_text_0 ..."""
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
        cron = dt.replace("T", " ") + ":00" if "T" in dt else dt
    else:
        cron = f.get("cron", "").strip()
    return dict(
        name        = f.get("name",        "").strip(),
        chat_id     = f.get("chat_id",     "").strip(),
        cron        = cron,
        msg_type    = f.get("msg_type",    "text"),
        msg_text    = f.get("msg_text",    "").strip() or None,
        msg_file_id = f.get("msg_file_id", "").strip() or None,
        msg_caption = f.get("msg_caption", "").strip() or None,
        once        = once,
    )


def _reload():
    try:
        import bot
        bot.reload_schedules()
    except Exception:
        pass


# ======== 统计页面 ========
@flask_app.route("/stats")
def stats_page():
    if not session.get("stats_auth"):
        return render_template("stats_login.html", err=False)
    return render_template(
        "stats.html",
        kw_logs  = db.get_keyword_logs(limit=200),
        sc_logs  = db.get_schedule_logs(limit=100),
        banned   = db.get_banned_users(),
        stats    = db.get_stats(),
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
    db.ban_user(
        uid,
        request.form.get("username",   ""),
        request.form.get("first_name", ""),
        request.form.get("reason",     ""),
    )
    return redirect("/stats")


@flask_app.route("/stats/unban/<int:uid>")
def stats_unban(uid):
    if not session.get("stats_auth"):
        return redirect("/stats")
    db.unban_user(uid)
    return redirect("/stats")


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


# ======== 调试用：列出所有路由（开发时可用） ========
@flask_app.route("/debug/routes")
def debug_routes():
    if not check_auth():
        return "unauthorized", 403
    routes = []
    for rule in flask_app.url_map.iter_rules():
        routes.append(f"{','.join(rule.methods)} {rule.rule}")
    return "<pre>" + "\n".join(sorted(routes)) + "</pre>"