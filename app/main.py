# app/main.py
import os
import logging
from datetime import datetime
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.utils import executor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from app.db import init_db, get_active_tasks
from app.bot_handlers import register_handlers, schedule_task_jobs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # ДОЛЖЕН содержать путь, например: https://.../webhook

if not BOT_TOKEN:
    raise SystemExit("⚠️ BOT_TOKEN не задан")
if not WEBHOOK_URL:
    raise SystemExit("⚠️ WEBHOOK_URL не задан")

# --- Разбираем URL и жёстко требуем путь ---
parsed = urlparse(WEBHOOK_URL)

if not parsed.path or parsed.path == "/":
    # Специально не даём запускаться с голым корнем, чтобы не было рассинхрона
    raise SystemExit(
        "⚠️ WEBHOOK_URL должен содержать путь, например:\n"
        "https://telegram-task-bot-team-final.onrender.com/webhook"
    )

WEBHOOK_PATH = parsed.path

WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.getenv("PORT", 10000))

logger.info(f"BOOT: WEBHOOK_URL={WEBHOOK_URL}, WEBHOOK_PATH={WEBHOOK_PATH}")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

scheduler = AsyncIOScheduler()
register_handlers(dp, scheduler)


async def on_startup(dp: Dispatcher):
    logger.info("🚀 on_startup: инициализируем БД, планировщик и вебхук")

    init_db()
    logger.info("✅ База инициализирована")

    tasks = get_active_tasks()
    for t in tasks:
        try:
            deadline = datetime.fromisoformat(t["deadline_ts"])
        except Exception:
            continue

        schedule_task_jobs(
            dp=dp,
            task_id=t["id"],
            chat_id=t["chat_id"],
            title=t["title"],
            deadline=deadline,
            scheduler=scheduler,
        )

    scheduler.start()
    logger.info("⏰ Планировщик запущен")

    # Ставим webhook РОВНО на WEBHOOK_URL (включая путь /webhook)
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"🌐 Webhook установлен: {WEBHOOK_URL}")


async def on_shutdown(dp: Dispatcher):
    logger.info("🛑 Остановка, удаляем webhook и гасим планировщик")

    try:
        await bot.delete_webhook()
    except Exception as e:
        logger.warning(f"Не удалось удалить webhook: {e}")

    try:
        scheduler.shutdown(wait=False)
    except Exception as e:
        logger.warning(f"Ошибка при остановке планировщика: {e}")

    await dp.storage.close()
    await dp.storage.wait_closed()
    await bot.session.close()


if __name__ == "__main__":
    logger.info("🌍 Запуск webhook-сервера через aiogram.executor")

    executor.start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,  # ← тот же путь, что и в WEBHOOK_URL
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        skip_updates=True,
        host=WEBAPP_HOST,
        port=WEBAPP_PORT,
    )
