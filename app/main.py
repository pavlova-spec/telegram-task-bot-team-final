import os
import logging

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from app.db import init_db, get_active_tasks
from app.bot_handlers import register_handlers, schedule_task_jobs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not BOT_TOKEN:
    raise SystemExit("⚠️ BOT_TOKEN не задан в переменных окружения")

if not WEBHOOK_URL:
    raise SystemExit("⚠️ WEBHOOK_URL не задан в переменных окружения")

# --- глобальные объекты бота ---
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

scheduler = AsyncIOScheduler()


async def on_startup(app: web.Application):
    """
    Старт приложения (Render дергает при запуске).
    """
    logger.info("🚀 Стартуем, инициализируем БД и webhook")

    # 1) БД
    init_db()
    logger.info("✅ База инициализирована")

    # 2) Регистрируем хэндлеры
    register_handlers(dp, scheduler)

    # 3) Стартуем планировщик
    scheduler.start()

    # 4) Рескейдим активные задачи
    tasks = get_active_tasks()
    for t in tasks:
        try:
            from datetime import datetime
            deadline = datetime.fromisoformat(t["deadline_ts"])
        except Exception:
            logger.exception(
                "❌ Не удалось преобразовать дедлайн у задачи %s", t["id"]
            )
            continue

        schedule_task_jobs(
            dp=dp,
            task_id=t["id"],
            chat_id=t["chat_id"],
            title=t["title"],
            deadline=deadline,
            scheduler=scheduler,
        )

    # 5) Ставим webhook
    await bot.set_webhook(WEBHOOK_URL)
    logger.info("✅ Webhook установлен на %s", WEBHOOK_URL)


async def on_shutdown(app: web.Application):
    """
    Аккуратное завершение при остановке.
    """
    logger.info("🛑 Остановка, удаляем webhook и гасим планировщик")

    try:
        scheduler.shutdown()
    except Exception:
        pass

    try:
        await bot.delete_webhook()
    except Exception:
        pass

    # предупреждение депрекейшена нам не мешает, но можно игнорировать
    await bot.session.close()


async def handle_webhook(request: web.Request) -> web.Response:
    """
    Приём апдейтов от Telegram.
    """
    data = await request.json()
    update = Update.to_object(data)
    await dp.process_update(update)
    return web.Response(text="OK")


def create_app() -> web.Application:
    app = web.Application()

    # Webhook можно слать и на /, и на /webhook — оба пути работают
    app.router.add_post("/", handle_webhook)
    app.router.add_post("/webhook", handle_webhook)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    logger.info("💡 Запускаем aiohttp на порту %s", port)
    web.run_app(create_app(), host="0.0.0.0", port=port)
