# app/main.py
import os
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.utils import executor

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from app.db import init_db, get_active_tasks
from app.bot_handlers import register_handlers, schedule_task_jobs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загружаем переменные окружения из .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise SystemExit("⚠️ BOT_TOKEN не задан в .env")

# --- Инициализация бота, диспетчера и планировщика ---

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

scheduler = AsyncIOScheduler()
scheduler.start()

# Регистрируем хэндлеры (передаём scheduler, как раньше)
register_handlers(dp, scheduler)


async def on_startup(dp: Dispatcher):
    """
    Запускается один раз при старте polling.
    Инициализируем БД и перезапланируем активные задачи.
    """
    init_db()
    logger.info("✅ База инициализирована")

    tasks = get_active_tasks()
    for t in tasks:
        try:
            deadline = datetime.fromisoformat(t["deadline_ts"])
        except Exception:
            # если дата битая — просто пропускаем
            continue

        schedule_task_jobs(
            dp=dp,
            task_id=t["id"],
            chat_id=t["chat_id"],
            title=t["title"],
            deadline=deadline,
            scheduler=scheduler,
        )


if __name__ == "__main__":
    logger.info("🚀 Бот запускается...")
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)