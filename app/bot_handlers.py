from datetime import datetime, timedelta

from aiogram import types, Dispatcher
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import (
    add_task,
    get_tasks,
    mark_done,
    add_completion,
    get_task,
    get_task_completions,
)


class TaskFSM(StatesGroup):
    """
    Один шаг: ждём строку вида
    "Сделать отчёт 28.10.2025 14:30"
    """
    waiting_single_line = State()


def main_menu() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Новая задача", "📋 Мои задачи")
    return kb


def register_handlers(dp: Dispatcher, scheduler: AsyncIOScheduler):
    # /start
    @dp.message_handler(commands=["start"])
    async def start_cmd(m: types.Message):
        await m.answer(
            f"🙌 Привет, {m.from_user.first_name}!\n\n"
            "Я твой бот-дедлайнер: помогу не забыть задачи, "
            "даже когда ты забываешь, что выспаться тоже задача 😎\n\n"
            "Жми кнопки снизу или кидай задачи в формате:\n"
            "<b>Сделать отчёт 28.10.2025 14:30</b>",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )

    # ────────────────────────────────
    # Кнопка «Новая задача»
    # ────────────────────────────────
    @dp.message_handler(lambda m: m.text and "Новая задача" in m.text)
    async def new_task(m: types.Message, state: FSMContext):
        await m.answer(
            "📝 Кидай задачу одной строкой:\n\n"
            "<b>Название задачи 28.10.2025 14:30</b>\n\n"
            "Без слэшей, без палок, только ты и твой дедлайн 😌",
            parse_mode="HTML",
        )
        await TaskFSM.waiting_single_line.set()

    # Обработка однострочного формата
    @dp.message_handler(state=TaskFSM.waiting_single_line)
    async def create_task_single_line(m: types.Message, state: FSMContext):
        text = (m.text or "").strip()

        # Ожидаем, что последние 16 символов — это "dd.mm.YYYY HH:MM"
        # Пример: "Сделать отчёт 28.10.2025 14:30"
        if len(text) < 17:
            await m.answer(
                "❌ Нужен формат:\n"
                "<b>Сделать отчёт 28.10.2025 14:30</b>",
                parse_mode="HTML",
            )
            return

        dt_str = text[-16:]  # "28.10.2025 14:30"
        title_part = text[:-16].strip()

        if not title_part:
            await m.answer(
                "❌ Не вижу названия задачи перед датой.\n"
                "Пример: <b>Сделать отчёт 28.10.2025 14:30</b>",
                parse_mode="HTML",
            )
            return

        try:
            deadline = datetime.strptime(dt_str, "%d.%m.%Y %H:%M")
        except ValueError:
            await m.answer(
                "❌ Не смог прочитать дату и время.\n"
                "Нужен формат: <b>28.10.2025 14:30</b>",
                parse_mode="HTML",
            )
            return

        title = title_part

        # записываем задачу в БД
        task_id = add_task(
            chat_id=m.chat.id,
            title=title,
            deadline=deadline,
            creator_id=m.from_user.id,
        )

        # планируем напоминания (3 дня, 1 день, день Х)
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
            f"Дедлайн: <b>{deadline.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
            "Если что, список задач — в кнопке <b>«📋 Мои задачи»</b>.",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )
        await state.finish()

    # ────────────────────────────────
    # Список задач
    # ────────────────────────────────
    @dp.message_handler(lambda m: m.text and "Мои задачи" in m.text)
    async def list_tasks(m: types.Message):
        rows = get_tasks(m.chat.id)  # sync вызов
        if not rows:
            await m.answer(
                "📭 Активных задач нет — можно официально прокрастинировать 🙌",
                reply_markup=main_menu(),
            )
            return

        text_lines = []
        kb = InlineKeyboardMarkup(row_width=2)

        for r in rows:
            dl = datetime.fromisoformat(r["deadline_ts"]).strftime("%d.%m.%Y %H:%M")

            # кто уже отметил выполнение
            completions = get_task_completions(r["id"])
            if completions:
                # Вариант А — показываем именно user_id тех, кто нажал кнопку
                ids_str = ", ".join(str(c["user_id"]) for c in completions)
                done_line = f"✅ Выполнили (user_id): {ids_str}"
            else:
                done_line = "⏳ Пока никто не отметил выполнение"

            block = (
                f"• <b>{r['title']}</b>\n"
                f"   🕒 до <b>{dl}</b>\n"
                f"   {done_line}"
            )
            text_lines.append(block)

            # инлайн-кнопки для этой задачи
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

    # ────────────────────────────────
    # /done 5  — старый способ закрыть задачу
    # ────────────────────────────────
    @dp.message_handler(commands=["done"])
    async def done_cmd(m: types.Message):
        parts = m.text.split()
        if len(parts) < 2:
            await m.answer("Используй: /done 5 (где 5 — номер задачи в БД)")
            return
        try:
            task_id = int(parts[1])
        except ValueError:
            await m.answer("ID должен быть числом")
            return
        mark_done(task_id)
        await m.answer(
            "🟢 Задача закрыта командой /done. Красавчик 👑",
            reply_markup=main_menu(),
        )

    # ────────────────────────────────
    # CALLBACK: "Я сделал(а)"
    # ────────────────────────────────
    @dp.callback_query_handler(lambda c: c.data and c.data.startswith("done:"))
    async def inline_mark_done(callback_query: types.CallbackQuery):
        data = callback_query.data.split(":", 1)
        if len(data) != 2:
            await callback_query.answer()
            return

        try:
            task_id = int(data[1])
        except ValueError:
            await callback_query.answer("Что-то не так с ID задачи 🤔", show_alert=True)
            return

        user = callback_query.from_user

        # фиксируем, что этот user_id выполнил задачу
        add_completion(task_id, user.id)

        await callback_query.answer(
            "Отметили, что ты выполнил(а) задачу ✅",
            show_alert=False,
        )

    # ────────────────────────────────
    # CALLBACK: "Закрыть задачу"
    # ────────────────────────────────
    @dp.callback_query_handler(lambda c: c.data and c.data.startswith("close:"))
    async def inline_close_task(callback_query: types.CallbackQuery):
        data = callback_query.data.split(":", 1)
        if len(data) != 2:
            await callback_query.answer()
            return

        try:
            task_id = int(data[1])
        except ValueError:
            await callback_query.answer("Некорректный ID задачи 🤔", show_alert=True)
            return

        mark_done(task_id)

        await callback_query.answer("Задача закрыта для всех 🟢", show_alert=False)

    # ────────────────────────────────
    # /close 5 — закрыть задачу для всех
    # ────────────────────────────────
    @dp.message_handler(commands=["close"])
    async def close_cmd(m: types.Message):
        parts = m.text.split(maxsplit=1)
        if len(parts) < 2:
            await m.answer(
                "Чтобы закрыть задачу для всех, используй: /close 5",
                parse_mode="Markdown",
            )
            return
        try:
            task_id = int(parts[1])
        except ValueError:
            await m.answer(
                "ID должен быть числом, например: /close 5",
                parse_mode="Markdown",
            )
            return

        task = get_task(task_id)
        if not task or task["chat_id"] != m.chat.id:
            await m.answer("❌ Задача с таким ID не найдена в этом чате.")
            return

        # меняем статус -> задача исчезнет из «Мои задачи»
        mark_done(task_id)

        await m.answer(
            f"🔒 Задача #{task_id} «{task['title']}» закрыта и больше не будет в списке.",
            reply_markup=main_menu(),
        )


# ────────────────────────────────
# Планирование напоминаний
# ────────────────────────────────
def schedule_task_jobs(
    dp: Dispatcher,
    task_id: int,
    chat_id: int,
    title: str,
    deadline: datetime,
    scheduler: AsyncIOScheduler,
):
    def make_text(offset: int) -> str:
        texts = {
            3: f"⏳ Через ТРИ дня дедлайн по задаче: «{title}»",
            1: f"⚡ Завтра сдавать: «{title}»",
            0: f"🔥 Сегодня дедлайн по: «{title}»",
        }
        return texts[offset]

    for offset in (3, 1, 0):
        remind_time = deadline - timedelta(days=offset)
        if remind_time > datetime.now():
            scheduler.add_job(
                dp.bot.send_message,
                trigger="date",
                run_date=remind_time,
                kwargs={
                    "chat_id": chat_id,
                    "text": make_text(offset),
                },
            )
