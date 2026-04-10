import logging, os, threading
from dotenv import load_dotenv

load_dotenv()  # 最先加载 .env

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PROXY     = os.getenv("PROXY", "").strip() or None
WEB_PORT  = int(os.getenv("WEB_PORT", 5000))

if not BOT_TOKEN:
    raise RuntimeError("❌ .env 中未配置 BOT_TOKEN")

def run_flask():
    """在子线程运行 Flask，不影响 Bot 主线程的 asyncio 事件循环"""
    from app import flask_app
    log.info(f"Web 管理后台启动 → http://127.0.0.1:{WEB_PORT}")
    flask_app.run(host="0.0.0.0", port=WEB_PORT, debug=False, use_reloader=False)

def main():
    # 1. 先在子线程启动 Flask
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

    # 2. 主线程运行 Bot（内含 asyncio 事件循环）
    from bot import build_app
    application = build_app(BOT_TOKEN, PROXY)
    log.info("Bot 启动中...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()