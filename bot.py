import re, logging, os, asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    ChatMemberHandler, filters, ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
import database as db
import bot_helpers as bh
import random

log = logging.getLogger(__name__)

bot_app   = None
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
ADMIN_IDS: set[int] = set()


def load_admin_ids():
    raw = os.getenv("ADMIN_IDS", "")
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ADMIN_IDS.add(int(part))
    log.info(f"管理员ID列表: {ADMIN_IDS}")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ======== 发送单条消息 ========
async def send_single(bot, chat_id, msg_type, text=None,
                      file_id=None, caption=None, reply_to=None):
    kw = {"chat_id": chat_id}
    if reply_to:
        kw["reply_to_message_id"] = reply_to
    t  = msg_type.lower()
    pm = "HTML"

    if caption:
        caption = caption.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
        caption = caption.replace("\\n", "\n")  # ✅ 加这行：字面量 \n → 真换行
    if text:
        text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
        text = text.replace("\\n", "\n")  # ✅ 加这行

    try:
        if t == "text":
            return await bot.send_message(text=text or "", parse_mode=pm, **kw)
        elif t == "photo":
            return await bot.send_photo(photo=file_id, caption=caption,
                                        parse_mode=pm if caption else None, **kw)
        elif t == "video":
            return await bot.send_video(video=file_id, caption=caption,
                                        parse_mode=pm if caption else None, **kw)
        elif t == "audio":
            return await bot.send_audio(audio=file_id, caption=caption,
                                        parse_mode=pm if caption else None, **kw)
        elif t == "document":
            return await bot.send_document(document=file_id, caption=caption,
                                           parse_mode=pm if caption else None, **kw)
        elif t == "animation":
            return await bot.send_animation(animation=file_id, caption=caption,
                                            parse_mode=pm if caption else None, **kw)
        elif t == "voice":
            return await bot.send_voice(voice=file_id, caption=caption,
                                        parse_mode=pm if caption else None, **kw)
        elif t == "sticker":
            return await bot.send_sticker(sticker=file_id, **kw)
        else:
            return await bot.send_message(text=f"[不支持的类型:{t}]", **kw)
    except Exception as e:
        log.error(f"send_single 失败 type={t}: {e}")
        raise


async def send_media(bot, chat_id, msg_type, text=None,
                     file_id=None, caption=None, reply_to=None):
    return await send_single(bot, chat_id, msg_type, text, file_id, caption, reply_to)


# ======== 检查 file_id 是否被软删除 ========
async def guarded_send(bot, chat_id, msg_type, text=None,
                       file_id=None, caption=None, reply_to=None):
    if file_id and msg_type != "text":
        if not db.is_file_id_active(file_id):
            log.warning(f"⚠️ file_id 已删除，跳过发送: {file_id[:30]}...")
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=(f"⚠️ <b>消息未发送</b>\n\n"
                              f"文件已从文件库删除，请更新对应关键词或定时任务。\n"
                              f"<code>{file_id[:50]}</code>"),
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
            return None
    return await send_media(bot, chat_id, msg_type, text, file_id, caption, reply_to)


# ======== 关键词匹配 ========
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.channel_post
    if not msg:
        return

    user    = msg.from_user
    user_id = user.id if user else 0

    if user and db.is_banned(user_id):
        return

    text = (msg.text or msg.caption or "").strip()
    if not text:
        return

    chat     = msg.chat
    is_group = chat.type in ("group", "supergroup", "channel")

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

        # 5秒冷却
        if not bh.check_kw_cooldown(kw["id"]):
            log.debug(f"关键词 #{kw['id']} 冷却中，跳过")
            return

        replies = kw.get("replies", [])
        if not replies:
            continue

        # 记录触发日志
        db.log_keyword_trigger(
            user_id        = user_id,
            username       = user.username if user else "",
            first_name     = user.first_name if user else "",
            chat_id        = chat.id,
            chat_title     = chat.title or getattr(chat, "first_name", "") or str(chat.id),
            chat_type      = chat.type,
            keyword_id     = kw["id"],
            keyword_pattern= kw["pattern"],
        )

        # 自动Ban检查
        if user_id:
            bh.record_trigger(user_id)
            for rule in db.get_auto_ban_rules():
                if not rule["active"]:
                    continue
                count = bh.get_trigger_count(user_id, rule["window_seconds"])
                if count >= rule["trigger_count"]:
                    db.ban_user(user_id,
                                user.username or "",
                                user.first_name or "",
                                f"自动Ban: {rule['window_seconds']}秒内触发{count}次")
                    for admin_id in ADMIN_IDS:
                        try:
                            await ctx.bot.send_message(
                                chat_id=admin_id,
                                text=(f"🚫 <b>自动封禁通知</b>\n\n"
                                      f"用户：{user.first_name} (<code>{user_id}</code>)\n"
                                      f"原因：{rule['window_seconds']}秒内触发 {count} 次"),
                                parse_mode="HTML"
                            )
                        except Exception:
                            pass
                    return

        # 发送回复
        mode         = kw.get("mode", "random")
        delete_after = kw.get("delete_after_seconds")

        async def do_send(r):
            sent = await guarded_send(
                ctx.bot, chat.id,
                r["reply_type"],
                r.get("reply_text"),
                r.get("reply_file_id"),
                r.get("reply_caption"),
                reply_to=msg.message_id,
            )
            if sent is None:
                return
            if is_group and r["reply_type"] == "text":
                delay = delete_after if delete_after else 10
                asyncio.create_task(bh.delete_later(ctx.bot, chat.id, sent.message_id, delay))
            elif delete_after:
                asyncio.create_task(bh.delete_later(ctx.bot, chat.id, sent.message_id, delete_after))

        if mode == "all":
            for r in replies:
                await do_send(r)
        else:
            await do_send(random.choice(replies))

        return


# ======== 私聊：文本→关键词，媒体→文件库 ========
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
        db.add_file_record(
            file_id, ftype, fname, fsize, mime, w, h, dur,
            uploader_id=user_id,
            uploader_name=user.first_name if user else "",
            uploader_username=user.username if user else "",
        )
        size_str = ""
        if fsize:
            size_str = f"{fsize/1048576:.1f} MB" if fsize > 1048576 else f"{fsize/1024:.1f} KB"
        parts = [x for x in [f"{w}×{h}" if w and h else "",
                              f"{dur}秒" if dur else "", size_str, fname or ""] if x]
        await msg.reply_text(
            f"✅ <b>{ftype}</b>  {' | '.join(parts)}\n\n<code>{file_id}</code>",
            parse_mode="HTML"
        )


# ======== 入群欢迎 ========
async def welcome_new_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result:
        return
    if result.new_chat_member.status not in ("member", "restricted"):
        return
    if result.old_chat_member.status in ("member", "administrator", "creator"):
        return
    new_user = result.new_chat_member.user
    mention  = f'<a href="tg://user?id={new_user.id}">{new_user.first_name}</a>'
    await ctx.bot.send_message(
        chat_id=result.chat.id,
        text=f"👋 欢迎 {mention} 加入！",
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
        lines.append(f"{status} <code>{kw['pattern']}</code> [{kw['match']}]")
    await update.message.reply_text(
        f"📋 <b>关键词列表（共{len(kws)}条）</b>\n\n" + "\n".join(lines),
        parse_mode="HTML"
    )


async def cmd_task_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    schedules = db.get_schedules()
    logs      = db.get_schedule_logs(limit=10)
    lines     = ["⏰ <b>定时任务状态</b>\n"]
    for s in schedules:
        status = "✅运行中" if s["active"] else "⏸已停用"
        once   = " 🔂一次性" if s["once"] else ""
        lines.append(f"{status}{once}  <b>{s['name'] or '未命名'}</b>\n"
                     f"   {s['cron']}  →  <code>{s['chat_id']}</code>")
    lines.append("\n📜 <b>最近执行记录</b>\n")
    for lg in logs:
        icon = {"done":"✅","running":"🔄","error":"❌","pending":"⏳"}.get(lg["status"],"❓")
        t    = lg["finished_at"] or lg["started_at"] or "—"
        lines.append(f"{icon} {lg['schedule_name'] or '未命名'}  <code>{t}</code>")
    if not logs:
        lines.append("暂无执行记录")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ======== 定时任务执行 ========
def make_job(sid, name, chat_id, msg_type, msg_text, msg_file_id, msg_caption,
             once, delete_after_seconds):
    async def job():
        log_id = db.log_schedule_start(sid, name)
        try:
            sent = await guarded_send(
                bot_app.bot, chat_id, msg_type, msg_text, msg_file_id, msg_caption
            )
            if sent and delete_after_seconds:
                asyncio.create_task(
                    bh.delete_later(bot_app.bot, chat_id, sent.message_id, delete_after_seconds)
                )
            db.log_schedule_done(log_id, success=True)
            log.info(f"✅ 定时任务执行完成 #{sid} [{name}]")
        except Exception as e:
            db.log_schedule_done(log_id, success=False, error=str(e))
            log.error(f"❌ 定时任务执行失败 #{sid} [{name}]: {e}")
        finally:
            if once:
                db.toggle_schedule(sid)
                try:
                    scheduler.remove_job(f"sched_{sid}")
                except Exception:
                    pass
                log.info(f"🗑 一次性任务已完成并停用 #{sid} [{name}]")
    return job


def reload_schedules():
    scheduler.remove_all_jobs()

    # 每分钟检查关键词是否到期
    scheduler.add_job(
        check_expired_keywords,
        IntervalTrigger(minutes=1),
        id="__expire_check__",
        replace_existing=True,
    )

    for s in db.get_schedules():
        # ⚠️ 关键修复：sqlite3.Row 不支持 .get()，统一转成 dict
        s = dict(s)

        if not s["active"]:
            log.info(f"跳过已停用任务 #{s['id']} [{s['name']}]")
            continue

        once = bool(s["once"])
        cron = s["cron"].strip()

        try:
            if once:
                # ⚠️ 关键修复：app.py 存入格式可能是 "2026-04-10 09:00:00"（含空格）
                # 或 "2026-04-10T09:00:00"，统一替换空格为 T 后解析
                cron_iso = cron.replace(" ", "T")
                run_dt   = datetime.fromisoformat(cron_iso)
                trigger  = DateTrigger(run_date=run_dt, timezone="Asia/Shanghai")
                log.info(f"一次性任务将在 {run_dt} 执行 #{s['id']} [{s['name']}]")
            else:
                parts = cron.split()
                if len(parts) != 5:
                    log.warning(
                        f"⚠️ cron格式错误 #{s['id']} [{s['name']}]: "
                        f"'{cron}' 需要5个字段，当前{len(parts)}个"
                    )
                    continue
                mi, hr, dm, mo, dw = parts
                trigger = CronTrigger(
                    minute=mi, hour=hr, day=dm, month=mo,
                    day_of_week=dw, timezone="Asia/Shanghai"
                )

            scheduler.add_job(
                make_job(
                    s["id"], s["name"], s["chat_id"],
                    s["msg_type"],
                    s["msg_text"],        # sqlite3.Row 用 [] 取值，已转 dict 安全
                    s["msg_file_id"],
                    s["msg_caption"],
                    once,
                    s.get("delete_after_seconds"),  # dict 支持 .get()
                ),
                trigger,
                id=f"sched_{s['id']}",
                replace_existing=True,
            )
            log.info(f"✅ 定时任务已加载 #{s['id']} [{s['name']}] once={once} cron={cron}")
        except Exception as e:
            log.error(f"❌ 定时任务加载失败 #{s['id']} [{s['name']}]: {e}")


# ======== 关键词到期检查 ========
async def check_expired_keywords():
    expired = db.get_expired_keywords()
    for kw in expired:
        db.deactivate_keyword(kw["id"])
        log.info(f"⏱ 关键词已到期停用 #{kw['id']} [{kw['pattern']}]")
        for admin_id in ADMIN_IDS:
            try:
                await bot_app.bot.send_message(
                    chat_id=admin_id,
                    text=(f"⏱ <b>关键词已到期</b>\n\n"
                          f"关键词 <code>{kw['pattern']}</code> 已自动停用。\n"
                          f"统计记录已保留，如需重启请前往管理后台。"),
                    parse_mode="HTML"
                )
            except Exception:
                pass


# ======== 启动 ========
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

    app.add_handler(ChatMemberHandler(welcome_new_member, ChatMemberHandler.CHAT_MEMBER))

    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & (
            filters.TEXT | filters.PHOTO | filters.VIDEO | filters.AUDIO |
            filters.Document.ALL | filters.ANIMATION | filters.VOICE | filters.Sticker.ALL
        ) & ~filters.COMMAND,
        handle_all
    ))

    app.add_handler(MessageHandler(
        (filters.ChatType.GROUPS | filters.ChatType.CHANNEL) &
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
        handle_message
    ))

    return app
