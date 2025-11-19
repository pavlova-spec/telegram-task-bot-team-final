# app/main.py
import os
import logging
from datetime import datetime

from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from app.db import init_db, get_active_tasks
from app.bot_handlers import register_handlers, schedule_task_jobs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------- ENV -----------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://...onrender.com/webhook
if not BOT_TOKEN:
    raise SystemExit("⚠️ BOT_TOKEN не задан в переменных окружения")
if not WEBHOOK_URL:
    raise SystemExit("⚠️ WEBHOOK_URL не задан в переменных окружения")

# Render передаёт порт в переменной PORT
APP_HOST = "0.0.0.0"
APP_PORT = int(os.getenv("PORT", "10000"))

# ----------------- BOT / DP / SCHEDULER -----------------
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

scheduler = AsyncIOScheduler()
scheduler.start()

# регистрируем хэндлеры, как и раньше
register_handlers(dp, scheduler)


# ----------------- WEBHOOK HANDLER -----------------
async def handle_webhook(request: web.Request):
    """
    Принимаем апдейт от Telegram и передаём его в Dispatcher.
    ВАЖНО: перед этим выставляем current bot / dispatcher,
    иначе m.answer() не знает, какой bot использовать.
    """
    # привязываем текущий bot и dp к контексту aiogram
    Bot.set_current(bot)
    Dispatcher.set_current(dp)

    data = await request.json()
    update = types.Update(**data)
    await dp.process_update(update)
    return web.Response(text="OK")


# ----------------- STARTUP / SHUTDOWN -----------------
async def on_startup(app: web.Application):
    logger.info("🚀 Старт приложения, инициализируем БД и webhook")

    # 1. БД
    init_db()
    logger.info("✅ База инициализирована")

    # 2. Webhook
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"🔗 Webhook установлен: {WEBHOOK_URL}")

    # 3. Рескейдим задачи из БД
    tasks = get_active_tasks()
    for t in tasks:
        try:
            deadline = datetime.fromisoformat(t["deadline_ts"])
        except Exception:
            logger.exception("❌ Не удалось прочитать дедлайн у задачи %s", t["id"])
            continue

        schedule_task_jobs(
            dp=dp,
            task_id=t["id"],
            chat_id=t["chat_id"],
            title=t["title"],
            deadline=deadline,
            scheduler=scheduler,
        )
    logger.info("🔁 Активные задачи рескейджены.")


async def on_shutdown(app: web.Application):
    logger.info("🛑 Остановка, удаляем webhook и гасим планировщик")
    await bot.delete_webhook()
    scheduler.shutdown(wait=False)
    await bot.session.close()


# ----------------- ENTRYPOINT -----------------
def main():
    app = web.Application()
    app.router.add_post("/webhook", handle_webhook)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    web.run_app(app, host=APP_HOST, port=APP_PORT)


if __name__ == "__main__":
    main()
