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

# -----------------------------------------
# Настройка логирования
# -----------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------------------
# Переменные окружения
# -----------------------------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_BASE = os.getenv("WEBHOOK_URL")  # например: https://telegram-task-bot-team-final.onrender.com

if not BOT_TOKEN:
    raise SystemExit("⚠️ BOT_TOKEN не задан в переменных окружения")

if not WEBHOOK_BASE:
    raise SystemExit("⚠️ WEBHOOK_URL не задан в переменных окружения")

# путь, на который Telegram шлёт апдейты
WEBHOOK_PATH = "/webhook"
# полный URL вебхука (то, что мы задаём через setWebhook)
WEBHOOK_URL = WEBHOOK_BASE.rstrip("/") + WEBHOOK_PATH

# Host/port для Render (он даёт PORT в env)
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.getenv("PORT", "10000"))

# -----------------------------------------
# Инициализация бота, диспетчера, планировщика
# -----------------------------------------
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

scheduler = AsyncIOScheduler()
scheduler.start()


# -----------------------------------------
# Хуки старта и остановки
# -----------------------------------------
async def on_startup(dispatcher: Dispatcher):
    logger.info("🚀 Запуск бота (webhook)...")

    # Инициализируем БД
    init_db()
    logger.info("✅ База инициализирована")

    # Регистрируем хэндлеры (кнопки, команды и т.п.)
    register_handlers(dp, scheduler)

    # Рескейдим активные задачи из БД
    tasks = get_active_tasks()
    for t in tasks:
        try:
            deadline = datetime.fromisoformat(t["deadline_ts"])
        except Exception:
            logger.exception("❌ Не удалось преобразовать дедлайн у задачи %s", t["id"])
            continue

        schedule_task_jobs(
            dp=dp,
            task_id=t["id"],
            chat_id=t["chat_id"],
            title=t["title"],
            deadline=deadline,
            scheduler=scheduler,
        )

    # Настраиваем webhook в Telegram
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"🌐 Webhook установлен: {WEBHOOK_URL}")


async def on_shutdown(dispatcher: Dispatcher):
    logger.info("🛑 Остановка, удаляем webhook и гасим планировщик")
    try:
        await bot.delete_webhook()
    except Exception:
        logger.exception("Ошибка при удалении webhook")

    try:
        scheduler.shutdown(wait=False)
    except Exception:
        logger.exception("Ошибка при остановке scheduler")

    await storage.close()
    await storage.wait_closed()


# -----------------------------------------
# Точка входа
# -----------------------------------------
if __name__ == "__main__":
    logger.info("💡 Бот запускается через webhook...")
    executor.start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        skip_updates=True,
        host=WEBAPP_HOST,
        port=WEBAPP_PORT,
    )
