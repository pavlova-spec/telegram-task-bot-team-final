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

# ─────────────────────────────────────────────
# Настройки логирования
# ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# ENV переменные
# ─────────────────────────────────────────────
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # например: https://telegram-task-bot-team-final.onrender.com
WEBHOOK_PATH = "/webhook"              # путь для Telegram
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.getenv("PORT", 10000))

if not BOT_TOKEN:
    raise SystemExit("⚠️ BOT_TOKEN не задан в .env или переменных окружения")
if not WEBHOOK_URL:
    raise SystemExit("⚠️ WEBHOOK_URL не задан в .env или переменных окружения")

FULL_WEBHOOK_URL = WEBHOOK_URL.rstrip("/") + WEBHOOK_PATH

# ─────────────────────────────────────────────
# Инициализация бота / диспетчера / планировщика
# ─────────────────────────────────────────────
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

scheduler = AsyncIOScheduler()

# Регистрируем хэндлеры
register_handlers(dp, scheduler)


# ─────────────────────────────────────────────
# on_startup / on_shutdown
# ─────────────────────────────────────────────
async def on_startup(dp: Dispatcher):
    """
    Вызывается один раз при старте webhook-сервера.
    """
    logger.info("🚀 Стартуем, инициализируем БД и вебхук")

    # Инициализируем БД
    init_db()
    logger.info("✅ База инициализирована")

    # Перепланируем активные задачи из БД
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

    # Стартуем планировщик напоминаний
    scheduler.start()
    logger.info("⏰ Планировщик запущен")

    # Ставим вебхук
    await bot.set_webhook(FULL_WEBHOOK_URL)
    logger.info(f"🌐 Webhook установлен: {FULL_WEBHOOK_URL}")


async def on_shutdown(dp: Dispatcher):
    """
    Аккуратное завершение работы.
    На Render это вызывается при остановке сервиса.
    """
    logger.info("🛑 Остановка, удаляем webhook и гасим планировщик")

    try:
        await bot.delete_webhook()
    except Exception as e:
        logger.warning(f"Не удалось удалить webhook: {e}")

    try:
        scheduler.shutdown(wait=False)
    except Exception as e:
        logger.warning(f"Ошибка при остановке планировщика: {e}")

    # Закрываем хранилище FSM
    await dp.storage.close()
    await dp.storage.wait_closed()

    # Закрываем сессию бота
    await bot.session.close()


# ─────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🌍 Запуск webhook-сервера через aiogram.executor")

    executor.start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        skip_updates=True,
        host=WEBAPP_HOST,
        port=WEBAPP_PORT,
    )
