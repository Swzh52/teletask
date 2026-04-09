import re, logging, json, os
from datetime import datetime
from telegram import Update, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import database as db
import random

log = logging.getLogger(__name__)

bot_app = None
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

# 从环境变量读取管理员 ID 列表
ADMIN_IDS = set()
def load_admin_ids():
    raw = os.getenv("ADMIN_IDS", "")
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ADMIN_IDS.add(int(part))
    log.info(f"管理员ID列表: {ADMIN_IDS}")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ======== 发送消息 ========
async def send_single(bot, chat_id, msg_type, text=None,
                       file_id=None, caption=None, reply_to=None):
    kw = {"chat_id": chat_id}
    if reply_to:
        kw["reply_to_message_id"] = reply_to
    t = msg_type.lower()
    try:
        if t == "text":
            await bot.send_message(text=text or "", **kw)
        elif t == "photo":
            await bot.send_photo(photo=file_id, caption=caption, **kw)
        elif t == "video":
            await bot.send_video(video=file_id, caption=caption, **kw)
        elif t == "audio":
            await bot.send_audio(audio=file_id, caption=caption, **kw)
        elif t == "document":
            await bot.send_document(document=file_id, caption=caption, **kw)
        elif t == "animation":
            await bot.send_animation(animation=file_id, caption=caption, **kw)
        elif t == "voice":
            await bot.send_voice(voice=file_id, caption=caption, **kw)
        elif t == "sticker":
            await bot.send_sticker(sticker=file_id, **kw)
        else:
            await bot.send_message(text=f"[不支持的类型:{t}]", **kw)
    except Exception as e:
        log.error(f"send_single 失败 type={t}: {e}")
        raise

async def send_media_group(bot, chat_id, items: list, reply_to=None):
    media = []
    for i, item in enumerate(items[:9]):
        t   = item.get("type", "photo")
        fid = item.get("file_id", "")
        cap = item.get("caption") if i == 0 else None
        if t == "photo":
            media.append(InputMediaPhoto(media=fid, caption=cap))
        elif t == "video":
            media.append(InputMediaVideo(media=fid, caption=cap))
        elif t == "document":
            media.append(InputMediaDocument(media=fid, caption=cap))
        elif t == "audio":
            media.append(InputMediaAudio(media=fid, caption=cap))
    if not media:
        return
    kw = {"chat_id": chat_id, "media": media}
    if reply_to:
        kw["reply_to_message_id"] = reply_to
    await bot.send_media_group(**kw)

async def send_media(bot, chat_id, msg_type, text=None,
                      file_id=None, caption=None, reply_to=None):
    if msg_type == "media_group":
        try:
            items = json.loads(file_id or "[]")
        except Exception:
            items = []
        await send_media_group(bot, chat_id, items, reply_to)
    else:
        await send_single(bot, chat_id, msg_type, text, file_id, caption, reply_to)

# ======== 关键词匹配（支持群组/频道） ========
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg  = update.message or update.channel_post
    if not msg:
        return

    user    = msg.from_user
    user_id = user.id if user else 0

    if user and db.is_banned(user_id):
        return

    text = (msg.text or msg.caption or "").strip()
    if not text:
        return

    chat = msg.chat
    for kw in db.get_keywords():
        if not kw["active"]:
            continue
        pattern, match_type = kw["pattern"], kw["match"]
        hit = False
        if match_type == "exact":
            hit = (text == pattern)
        elif match_type == "contains":
            hit = (pattern.lower() in text.lower())
        elif match_type == "regex":
            try:
                hit = bool(re.search(pattern, text, re.IGNORECASE))
            except Exception:
                pass

        if not hit:
            continue

        replies = kw.get("replies", [])
        if not replies:
            continue

        # 记录触发日志
        db.log_keyword_trigger(
            user_id        = user_id,
            username       = user.username if user else "",
            first_name     = user.first_name if user else "",
            chat_id        = chat.id,
            chat_title     = chat.title or chat.first_name or str(chat.id),
            chat_type      = chat.type,
            keyword_id     = kw["id"],
            keyword_pattern= kw["pattern"],
        )

        mode = kw.get("mode", "random")

        if mode == "all":
            # 全部发送
            for r in replies:
                await send_media(
                    ctx.bot, chat.id,
                    r["reply_type"],
                    r.get("reply_text"),
                    r.get("reply_file_id"),
                    r.get("reply_caption"),
                    reply_to=msg.message_id,
                )
        else:
            # 随机选一条
            r = random.choice(replies)
            await send_media(
                ctx.bot, chat.id,
                r["reply_type"],
                r.get("reply_text"),
                r.get("reply_file_id"),
                r.get("reply_caption"),
                reply_to=msg.message_id,
            )
        return  # 命中第一个关键词后停止


# ======== 媒体上传（记录上传者） ========
async def handle_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        await handle_message(update, ctx)
        return

    user    = msg.from_user
    user_id = user.id if user else 0

    if msg.text and not msg.text.startswith("/"):
        await handle_message(update, ctx)
        return

    if not is_admin(user_id):
        await msg.reply_text("⚠️ 只有管理员才能上传文件获取 file_id。")
        return

    file_id = ftype = fname = fsize = mime = w = h = dur = None

    if msg.photo:
        p = msg.photo[-1]
        file_id, ftype = p.file_id, "photo"
        w, h, fsize = p.width, p.height, p.file_size
        fname = f"photo_{p.file_unique_id}.jpg"
    elif msg.video:
        v = msg.video
        file_id, ftype = v.file_id, "video"
        w, h, dur, fsize, mime = v.width, v.height, v.duration, v.file_size, v.mime_type
        fname = v.file_name or f"video_{v.file_unique_id}"
    elif msg.audio:
        a = msg.audio
        file_id, ftype = a.file_id, "audio"
        dur, fsize, mime = a.duration, a.file_size, a.mime_type
        fname = a.file_name or f"audio_{a.file_unique_id}"
    elif msg.document:
        d = msg.document
        file_id, ftype = d.file_id, "document"
        fsize, mime = d.file_size, d.mime_type
        fname = d.file_name or f"doc_{d.file_unique_id}"
    elif msg.animation:
        a = msg.animation
        file_id, ftype = a.file_id, "animation"
        w, h, dur, fsize = a.width, a.height, a.duration, a.file_size
        fname = a.file_name or f"gif_{a.file_unique_id}"
    elif msg.voice:
        v = msg.voice
        file_id, ftype = v.file_id, "voice"
        dur, fsize, mime = v.duration, v.file_size, v.mime_type
        fname = f"voice_{v.file_unique_id}.ogg"
    elif msg.sticker:
        s = msg.sticker
        file_id, ftype = s.file_id, "sticker"
        w, h = s.width, s.height
        fname = f"sticker_{s.file_unique_id}"

    if file_id:
        # 记录上传者信息
        db.add_file_record(
            file_id, ftype, fname, fsize, mime, w, h, dur,
            uploader_id       = user_id,
            uploader_name     = user.first_name if user else "",
            uploader_username = user.username if user else "",
        )
        size_str = ""
        if fsize:
            size_str = f"{fsize/1048576:.1f} MB" if fsize > 1048576 else f"{fsize/1024:.1f} KB"
        parts = [x for x in [
            f"{w}×{h}" if w and h else "",
            f"{dur}秒" if dur else "",
            size_str,
            fname or "",
        ] if x]
        await msg.reply_text(
            f"✅ <b>{ftype}</b>  {' | '.join(parts)}\n\n"
            f"<code>{file_id}</code>",
            parse_mode="HTML"
        )

# ======== 管理员命令 ========
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("👋 你好！我是自动回复机器人。")
        return
    kw_count = len(db.get_keywords())
    sc_count  = len(db.get_schedules())
    await update.message.reply_text(
        f"🤖 <b>Bot 管理助手</b>\n\n"
        f"👤 管理员：{user.first_name}\n"
        f"📋 关键词规则：{kw_count} 条\n"
        f"⏰ 定时任务：{sc_count} 条\n\n"
        f"<b>可用命令：</b>\n"
        f"/keywords — 查看关键词列表\n"
        f"/task_status — 查看定时任务状态\n\n"
        f"直接发送媒体文件即可获取 file_id。",
        parse_mode="HTML"
    )

async def cmd_keywords(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    kws = db.get_keywords()
    if not kws:
        await update.message.reply_text("暂无关键词规则。")
        return
    lines = []
    for kw in kws:
        status = "✅" if kw["active"] else "❌"
        lines.append(f"{status} <code>{kw['pattern']}</code> [{kw['match']}] → {kw['reply_type']}")
    await update.message.reply_text(
        f"📋 <b>关键词列表（共{len(kws)}条）</b>\n\n" + "\n".join(lines),
        parse_mode="HTML"
    )

async def cmd_task_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    schedules = db.get_schedules()
    logs      = db.get_schedule_logs(limit=20)

    if not schedules:
        await update.message.reply_text("暂无定时任务。")
        return

    lines = ["⏰ <b>定时任务状态</b>\n"]
    for s in schedules:
        status = "✅运行中" if s["active"] else "⏸已停用"
        once   = " 🔂一次性" if s["once"] else ""
        lines.append(f"{status}{once}  <b>{s['name'] or '未命名'}</b>\n"
                     f"   {s['cron']}  →  {s['chat_id']}")

    lines.append("\n📜 <b>最近执行记录</b>\n")
    if logs:
        for lg in logs:
            icon = {"done":"✅","running":"🔄","error":"❌","pending":"⏳"}.get(lg["status"],"❓")
            t = lg["finished_at"] or lg["started_at"] or ""
            lines.append(f"{icon} {lg['schedule_name']} — {t}")
    else:
        lines.append("暂无执行记录")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

# ======== 定时任务执行 ========
def make_job(sid, name, chat_id, msg_type, msg_text, msg_file_id, msg_caption, once):
    async def job():
        log_id = db.log_schedule_start(sid, name)
        try:
            await send_media(bot_app.bot, chat_id, msg_type,
                              msg_text, msg_file_id, msg_caption)
            db.log_schedule_done(log_id, success=True)
            log.info(f"✅ 定时任务执行完成 #{sid} [{name}]")
        except Exception as e:
            db.log_schedule_done(log_id, success=False, error=str(e))
            log.error(f"❌ 定时任务执行失败 #{sid} [{name}]: {e}")
        finally:
            if once:
                # 一次性任务：执行后标记停用
                db.toggle_schedule(sid)
                scheduler.remove_job(f"sched_{sid}")
                log.info(f"🗑 一次性任务已完成并停用 #{sid} [{name}]")
    return job

def reload_schedules():
    scheduler.remove_all_jobs()
    for s in db.get_schedules():
        if not s["active"]:
            log.info(f"跳过已停用任务 #{s['id']} [{s['name']}]")
            continue

        once  = bool(s["once"])
        cron  = s["cron"].strip()
        parts = cron.split()

        try:
            if once and len(parts) == 1:
                # 一次性任务：cron 字段存的是 ISO 日期时间字符串
                run_dt = datetime.fromisoformat(cron)
                trigger = DateTrigger(run_date=run_dt, timezone="Asia/Shanghai")
            elif len(parts) == 5:
                mi, hr, dm, mo, dw = parts
                trigger = CronTrigger(minute=mi, hour=hr, day=dm, month=mo,
                                       day_of_week=dw, timezone="Asia/Shanghai")
            else:
                log.warning(f"⚠️ cron格式错误 #{s['id']}: '{cron}'")
                continue

            scheduler.add_job(
                make_job(s["id"], s["name"], s["chat_id"],
                          s["msg_type"], s["msg_text"],
                          s["msg_file_id"], s["msg_caption"], once),
                trigger,
                id=f"sched_{s['id']}",
                replace_existing=True,
            )
            log.info(f"✅ 定时任务已加载 #{s['id']} [{s['name']}] cron={cron} once={once}")
        except Exception as e:
            log.error(f"❌ 定时任务加载失败 #{s['id']}: {e}")

async def post_init(application):
    global bot_app
    bot_app = application
    load_admin_ids()
    reload_schedules()
    scheduler.start()
    log.info("Bot 启动完成")

def build_app(token, proxy=None):
    builder = Application.builder().token(token).post_init(post_init)
    if proxy:
        builder = builder.proxy(proxy).get_updates_proxy(proxy)
    app = builder.build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("keywords",    cmd_keywords))
    app.add_handler(CommandHandler("task_status", cmd_task_status))

    # 私聊：文本 + 媒体
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & (
            filters.TEXT | filters.PHOTO | filters.VIDEO | filters.AUDIO |
            filters.Document.ALL | filters.ANIMATION | filters.VOICE | filters.Sticker.ALL
        ) & ~filters.COMMAND,
        handle_all
    ))

    # 群组消息：关键词匹配
    app.add_handler(MessageHandler(
        (filters.ChatType.GROUPS | filters.ChatType.CHANNEL) &
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
        handle_message
    ))

    return app