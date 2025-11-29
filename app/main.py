# app/main.py
import os
import logging
from datetime import datetime
from urllib.parse import urlparse, urlunparse

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
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Например: https://telegram-task-bot-team-final.onrender.com

if not BOT_TOKEN:
    raise SystemExit("⚠️ BOT_TOKEN не задан")
if not WEBHOOK_URL:
    raise SystemExit("⚠️ WEBHOOK_URL не задан")

# ─────────────────────────────────────────
# Нормализуем WEBHOOK_URL и WEBHOOK_PATH
# ─────────────────────────────────────────
parsed = urlparse(WEBHOOK_URL)

# Если путь пустой или просто "/", принудительно вешаем "/webhook"
if not parsed.path or parsed.path == "/":
    WEBHOOK_PATH = "/webhook"
    parsed = parsed._replace(path=WEBHOOK_PATH)
    WEBHOOK_URL = urlunparse(parsed)
else:
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

    # Ставим webhook на нормализованный WEBHOOK_URL
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"🌐 Webhook установлен: {WEBHOOK_URL}")


async def on_shutdown(dp: Dispatcher):
    logger.info("🛑 Остановка, гасим планировщик и ресурсы (webhook НЕ трогаем)")

    try:
        scheduler.shutdown(wait=False)
    except Exception as e:
        logger.warning(f"Ошибка при остановке планировщика: {e}")

    await dp.storage.close()
    await dp.storage.wait_closed()

    # Убираем deprecated доступ к bot.session, но если хочешь – можно оставить как было
    session = await bot.get_session()
    await session.close()

if __name__ == "__main__":
    logger.info("🌍 Запуск webhook-сервера через aiogram.executor")

    app = executor.get_app()          # получаем веб-приложение
    app.router.add_get("/", handle_root)  # добавляем ответ на GET /

    executor.start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        skip_updates=True,
        host=WEBAPP_HOST,
        port=WEBAPP_PORT,
    )
