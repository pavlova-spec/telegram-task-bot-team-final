from datetime import datetime, timedelta
import logging   # ← добавлено

from aiogram import types, Dispatcher
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import (
    add_task,
    get_tasks,
    mark_done,
    add_completion,
    get_task,
    get_task_completions,
)

logger = logging.getLogger(__name__)  # ← добавлено


class TaskFSM(StatesGroup):
    waiting_single_line = State()


def main_menu() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Новая задача", "📋 Мои задачи")
    return kb


def register_handlers(dp: Dispatcher, scheduler: AsyncIOScheduler):

    # 🔍 ОТЛАДКА: логируем ВСЕ входящие сообщения и из ЛС, и из групп
    @dp.message_handler(content_types=types.ContentTypes.ANY)
    async def debug_all_messages(m: types.Message):
        logger.info(
            f"[DEBUG] incoming: chat_id={m.chat.id}, "
            f"type={m.chat.type}, "
            f"from={m.from_user.id if m.from_user else 'None'}, "
            f"text={getattr(m, 'text', None)!r}"
        )

    # -------------- /start -------------------
    @dp.message_handler(commands=["start"])
    async def start_cmd(m: types.Message):
        await m.answer(
            f"🙌 Привет, {m.from_user.first_name}!\n\n"
            "Жми кнопки снизу или кидай задачу одной строкой:\n"
            "<b>Сделать отчёт 28.10.2025 14:30</b>",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )

    @dp.message_handler(lambda m: m.text == "➕ Новая задача")
    async def new_task(m: types.Message, state: FSMContext):
        await m.answer(
            "📝 Кидай задачу одной строкой:\n\n"
            "<b>Название задачи 28.10.2025 14:30</b>",
            parse_mode="HTML",
        )
        await TaskFSM.waiting_single_line.set()

    @dp.message_handler(state=TaskFSM.waiting_single_line)
    async def create_task_single_line(m: types.Message, state: FSMContext):
        text = m.text.strip()

        if len(text) < 17:
            await m.answer(
                "❌ Нужен формат: <b>Сделать отчёт 28.10.2025 14:30</b>",
                parse_mode="HTML",
            )
            return

        dt_str = text[-16:]
        title_part = text[:-16].strip()

        if not title_part:
            await m.answer(
                "❌ Нет названия перед датой.",
                parse_mode="HTML",
            )
            return

        try:
            deadline = datetime.strptime(dt_str, "%d.%m.%Y %H:%M")
        except ValueError:
            await m.answer("❌ Неверная дата/время.", parse_mode="HTML")
            return

        title = title_part
        task_id = add_task(
            chat_id=m.chat.id,
            title=title,
            deadline=deadline,
            creator_id=m.from_user.id,
        )

        schedule_task_jobs(
            dp=dp,
            task_id=task_id,
            chat_id=m.chat.id,
            title=title,
            deadline=deadline,
            scheduler=scheduler,
        )

        await m.answer(
            f"✅ Задача «<b>{title}</b>» сохранена.\n"
            f"⏰ Дедлайн: <b>{deadline.strftime('%d.%m.%Y %H:%M')}</b>",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )
        await state.finish()

    @dp.message_handler(lambda m: m.text == "📋 Мои задачи")
    async def list_tasks(m: types.Message):
        rows = get_tasks(m.chat.id)
        if not rows:
            await m.answer("📭 Активных задач нет 🙌", reply_markup=main_menu())
            return

        text_lines = []
        kb = InlineKeyboardMarkup(row_width=2)

        async def get_display_name(user_id: int) -> str:
            try:
                member = await m.bot.get_chat_member(m.chat.id, user_id)
                u = member.user
                if u.username:
                    return f"@{u.username}"
                full = (u.first_name or "") + (" " + u.last_name if u.last_name else "")
                return full.strip() or str(user_id)
            except Exception:
                return str(user_id)

        for r in rows:
            dl = datetime.fromisoformat(r["deadline_ts"]).strftime("%d.%m.%Y %H:%M")

            completions = get_task_completions(r["id"])
            user_ids = []
            seen = set()
            for c in completions:
                if c["user_id"] not in seen:
                    seen.add(c["user_id"])
                    user_ids.append(c["user_id"])

            if user_ids:
                show = user_ids[:3]
                names = [await get_display_name(uid) for uid in show]
                extra = len(user_ids) - len(show)
                if extra > 0:
                    done_line = f"✅ Выполнили: {', '.join(names)} и ещё {extra}"
                else:
                    done_line = f"✅ Выполнили: {', '.join(names)}"
            else:
                done_line = "⏳ Пока никто не отметил выполнение"

            block = (
                f"• <b>{r['title']}</b>\n"
                f"   🕒 до <b>{dl}</b>\n"
                f"   {done_line}"
            )
            text_lines.append(block)

            kb.add(
                InlineKeyboardButton(
                    text=f"✅ Я сделал(а): {r['title'][:20]}",
                    callback_data=f"done:{r['id']}",
                ),
                InlineKeyboardButton(
                    text="🔒 Закрыть задачу",
                    callback_data=f"close:{r['id']}",
                ),
            )

        await m.answer(
            "🗓 <b>Активные задачи:</b>\n\n" + "\n\n".join(text_lines),
            reply_markup=kb,
            parse_mode="HTML",
        )

    @dp.message_handler(commands=["done"])
    async def done_cmd(m: types.Message):
        parts = m.text.split()
        if len(parts) < 2:
            await m.answer("Используй: /done 5")
            return
        try:
            task_id = int(parts[1])
        except ValueError:
            await m.answer("ID должен быть числом")
            return
        mark_done(task_id)
        await m.answer("🟢 Задача закрыта.", reply_markup=main_menu())

    @dp.callback_query_handler(lambda c: c.data.startswith("done:"))
    async def inline_mark_done(callback_query: types.CallbackQuery):
        task_id = int(callback_query.data.split(":", 1)[1])
        user = callback_query.from_user
        add_completion(task_id, user.id)
        await callback_query.answer("Отметили выполнение ✅")

    @dp.callback_query_handler(lambda c: c.data.startswith("close:"))
    async def inline_close_task(callback_query: types.CallbackQuery):
        task_id = int(callback_query.data.split(":", 1)[1])
        mark_done(task_id)
        await callback_query.answer("Задача закрыта 🟢")

    @dp.message_handler(commands=["close"])
    async def close_cmd(m: types.Message):
        parts = m.text.split(maxsplit=1)
        if len(parts) < 2:
            await m.answer("Используй: /close 5")
            return
        task_id = int(parts[1])
        task = get_task(task_id)
        if not task or task["chat_id"] != m.chat.id:
            await m.answer("❌ Нет такой задачи")
            return
        mark_done(task_id)
        await m.answer(f"🔒 Задача {task_id} закрыта.", reply_markup=main_menu())


def schedule_task_jobs(dp, task_id, chat_id, title, deadline, scheduler):
    def make_text(offset):
        return {
            3: f"⏳ Через 3 дня дедлайн: «{title}»",
            1: f"⚡ Завтра дедлайн: «{title}»",
            0: f"🔥 Сегодня дедлайн: «{title}»",
        }[offset]

    for offset in (3, 1, 0):
        remind_time = deadline - timedelta(days=offset)
        if remind_time > datetime.now():
            scheduler.add_job(
                dp.bot.send_message,
                trigger="date",
                run_date=remind_time,
                kwargs={"chat_id": chat_id, "text": make_text(offset)},
            )
