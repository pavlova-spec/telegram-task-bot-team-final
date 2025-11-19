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

# ─────────────────────────────────────────────
# ENV
# ─────────────────────────────────────────────

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("⚠️ BOT_TOKEN не задан (ни в .env, ни в переменных окружения)")

# Полный URL webhook’а, например:
# https://telegram-task-bot-team-final.onrender.com/webhook
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if not WEBHOOK_URL:
    raise SystemExit("⚠️ WEBHOOK_URL не задан в переменных окружения")

# Порт, который даёт Render (или 8000 локально)
PORT = int(os.getenv("PORT", "8000"))

# ─────────────────────────────────────────────
# Инициализация бота / диспетчера / шедулера
# ─────────────────────────────────────────────

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

scheduler = AsyncIOScheduler()
scheduler.start()

# регистрируем все хэндлеры как раньше
register_handlers(dp, scheduler)

# ─────────────────────────────────────────────
# Жизненный цикл приложения (webhook)
# ─────────────────────────────────────────────

async def on_startup(app: web.Application):
    logger.info("🚀 Запуск бота (webhook-режим)...")

    # БД
    init_db()
    logger.info("✅ База инициализирована")

    # Рескейдим активные задачи
    tasks = get_active_tasks()
    for t in tasks:
        try:
            deadline = datetime.fromisoformat(t["deadline_ts"])
        except Exception:
            logger.exception("Не смогли распарсить дедлайн у задачи %s", t["id"])
            continue

        schedule_task_jobs(
            dp=dp,
            task_id=t["id"],
            chat_id=t["chat_id"],
            title=t["title"],
            deadline=deadline,
            scheduler=scheduler,
        )

    # Регистрируем webhook в Telegram
    await bot.set_webhook(WEBHOOK_URL)
    logger.info("✅ Webhook установлен: %s", WEBHOOK_URL)


async def on_shutdown(app: web.Application):
    logger.info("🔻 Остановка бота...")
    await bot.delete_webhook()
    await bot.session.close()
    scheduler.shutdown(wait=False)
    logger.info("🔻 Бот корректно завершён")


# ─────────────────────────────────────────────
# HTTP-обработчик для Telegram
# ─────────────────────────────────────────────

async def handle_webhook(request: web.Request) -> web.Response:
    """
    Сюда Telegram шлёт апдейты POST-запросом.
    """
    data = await request.json()
    update = types.Update.to_object(data)
    await dp.process_update(update)
    return web.Response(text="ok")


def main():
    app = web.Application()
    # путь должен совпадать с тем, что в WEBHOOK_URL (после домена)
    app.router.add_post("/webhook", handle_webhook)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # Render смотрит, что мы слушаем этот порт
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
