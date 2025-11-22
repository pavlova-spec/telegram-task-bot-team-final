# app/main.py
import os
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from aiohttp import web

from app.db import init_db, get_active_tasks
from app.bot_handlers import register_handlers, schedule_task_jobs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not BOT_TOKEN:
    raise SystemExit("⚠️ BOT_TOKEN не задан в .env / переменных окружения")
if not WEBHOOK_URL:
    raise SystemExit("⚠️ WEBHOOK_URL не задан в переменных окружения")

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# контекст для aiogram 2.x
Bot.set_current(bot)
Dispatcher.set_current(dp)

scheduler = AsyncIOScheduler()


async def on_startup(app: web.Application):
    logger.info("🚀 Запуск приложения, настраиваем webhook и БД")
    init_db()

    # запускаем планировщик
    scheduler.start()

    # перезапланируем активные задачи
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

    # чистим старый вебхук и ставим новый
    await bot.delete_webhook()
    await bot.set_webhook(WEBHOOK_URL)
    logger.info("✅ Webhook установлен: %s", WEBHOOK_URL)


async def on_shutdown(app: web.Application):
    logger.info("🛑 Остановка, удаляем webhook и гасим планировщик")
    scheduler.shutdown(wait=False)
    await bot.delete_webhook()
    await bot.session.close()


async def handle_webhook(request: web.Request):
    data = await request.json()
    update = Update.to_object(data)
    await dp.process_update(update)
    return web.Response(text="ok")


def create_app() -> web.Application:
    app = web.Application()

    # регистрируем все хэндлеры
    register_handlers(dp, scheduler)

    # маршруты вебхука — принимаем и на /, и на /webhook
    app.router.add_post("/", handle_webhook)
    app.router.add_post("/webhook", handle_webhook)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("PORT", "10000"))
    web.run_app(app, host="0.0.0.0", port=port)
