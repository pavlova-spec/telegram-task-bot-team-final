# app/bot_handlers.py
import logging
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

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
    save_last_action,
    get_last_action,
    clear_last_action,
    restore_task_status,
    delete_completion,
)

logger = logging.getLogger(__name__)
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


class TaskFSM(StatesGroup):
    """
    Один шаг: ждём строку вида
    "Сделать отчёт 28.10.2025 14:30"
    (режим по кнопке «Новая задача»)
    """
    waiting_single_line = State()


def main_menu() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Новая задача", "📋 Мои задачи")
    kb.add("↩️ Отменить последнее")
    return kb


def register_handlers(dp: Dispatcher, scheduler: AsyncIOScheduler):
    # /start
    @dp.message_handler(commands=["start"])
    async def start_cmd(m: types.Message):
        await m.answer(
            f"🙌 Привет, {m.from_user.first_name}!\n\n"
            "Я твой бот-дедлайнер: помогу не забыть задачи, "
            "даже когда ты забываешь, что выспаться тоже задача 😎\n\n"
            "Можешь просто кинуть строку вида:\n"
            "<b>Сделать отчёт 28.10.2025 14:30</b>\n"
            "или воспользоваться кнопками ниже 👇",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )

    # ────────────────────────────────
    # Кнопка «Новая задача»
    # ────────────────────────────────
    @dp.message_handler(lambda m: m.text == "➕ Новая задача")
    async def new_task(m: types.Message, state: FSMContext):
        await m.answer(
            "📝 Кидай задачу одной строкой:\n\n"
            "<b>Название задачи 28.10.2025 14:30</b>\n\n"
            "Без слэшей, без палок, только ты и твой дедлайн 😌",
            parse_mode="HTML",
        )
        await TaskFSM.waiting_single_line.set()

    # Обработка однострочного формата (FSM после кнопки)
    @dp.message_handler(state=TaskFSM.waiting_single_line)
    async def create_task_single_line(m: types.Message, state: FSMContext):
        text = (m.text or "").strip()

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

        task_id = add_task(
            chat_id=m.chat.id,
            title=title,
            deadline=deadline,
            creator_id=m.from_user.id,
        )

        # сохраняем последнее действие (добавление задачи)
        save_last_action(
            chat_id=m.chat.id,
            user_id=m.from_user.id,
            action_type="add_task",
            task_id=task_id,
            completion_id=None,
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
            f"Дедлайн: <b>{deadline.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
            "Если что, список задач — в кнопке <b>«📋 Мои задачи»</b>.",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )
        await state.finish()

    # ────────────────────────────────
    # Кнопка «Мои задачи»
    # ────────────────────────────────
    @dp.message_handler(lambda m: m.text == "📋 Мои задачи")
    async def list_tasks(m: types.Message):
        rows = get_tasks(m.chat.id)
        if not rows:
            await m.answer(
                "📭 Активных задач нет — можно официально прокрастинировать 🙌",
                reply_markup=main_menu(),
            )
            return

        text_lines = []
        kb = InlineKeyboardMarkup(row_width=2)

        for idx, r in enumerate(rows, start=1):
            dl = datetime.fromisoformat(r["deadline_ts"]).strftime("%d.%m.%Y %H:%M")

            # --- кто уже отметил выполнение ---
            completions = get_task_completions(r["id"])
            if completions:
                users_str = []
                for c in completions:
                    user_id = c["user_id"]
                    try:
                        tg_user = await dp.bot.get_chat(user_id)
                        if tg_user.username:
                            users_str.append(f"@{tg_user.username}")
                        else:
                            users_str.append(tg_user.full_name)
                    except Exception as e:
                        logger.warning(
                            "Не смогли получить данные пользователя %s: %s",
                            user_id,
                            e,
                        )
                        users_str.append(f"ID:{user_id}")

                done_line = "✅ Выполнили: " + ", ".join(users_str)
            else:
                done_line = "⏳ Пока никто не отметил выполнение"

            # --- блок текста по задаче с номером ---
            block = (
                f"{idx}. <b>{r['title']}</b>\n"
                f"   🕒 до <b>{dl}</b>\n"
                f"   {done_line}"
            )
            text_lines.append(block)

            # инлайн-кнопки для этой задачи — Вариант B: "3 ✅" и "3 🔒"
            kb.add(
                InlineKeyboardButton(
                    text=f"{idx} ✅",
                    callback_data=f"done:{r['id']}",
                ),
                InlineKeyboardButton(
                    text=f"{idx} 🔒",
                    callback_data=f"close:{r['id']}",
                ),
            )

        await m.answer(
            "🗓 <b>Активные задачи:</b>\n\n" + "\n\n".join(text_lines),
            reply_markup=kb,
            parse_mode="HTML",
        )

    # ────────────────────────────────
    # ГЛОБАЛЬНЫЙ ОДНОСТРОЧНЫЙ ВВОД (в любом чате, без FSM)
    # ────────────────────────────────
    @dp.message_handler(
        lambda m: m.text and not m.text.startswith("/"),
        state=None,
    )
    async def inline_task_anywhere(m: types.Message):
        """
        Любое сообщение без / и без FSM-состояния пробуем
        распарсить как: "Название задачи 28.10.2025 14:30".
        Если не получилось — тихо игнорируем.
        """
        text = m.text.strip()

        # Не трогаем тексты кнопок
        if text in ("➕ Новая задача", "📋 Мои задачи", "↩️ Отменить последнее"):
            return

        if len(text) < 17:
            logger.info("INLINE PARSE SKIP (too short): %r", text)
            return

        dt_str = text[-16:]
        title_part = text[:-16].strip()

        if not title_part:
            logger.info("INLINE PARSE SKIP (no title): %r", text)
            return

        try:
            deadline = datetime.strptime(dt_str, "%d.%m.%Y %H:%M")
        except ValueError:
            logger.info("INLINE PARSE SKIP (bad datetime): %r", text)
            return

        title = title_part

        task_id = add_task(
            chat_id=m.chat.id,
            title=title,
            deadline=deadline,
            creator_id=m.from_user.id,
        )
        
        # сохраняем последнее действие (добавление задачи)
        save_last_action(
            chat_id=m.chat.id,
            user_id=m.from_user.id,
            action_type="add_task",
            task_id=task_id,
            completion_id=None,
        )

        schedule_task_jobs(
            dp=dp,
            task_id=task_id,
            chat_id=m.chat.id,
            title=title,
            deadline=deadline,
            scheduler=scheduler,
        )

        logger.info(
            "INLINE TASK CREATED: chat_id=%s task_id=%s title=%r deadline=%s",
            m.chat.id,
            task_id,
            title,
            deadline.isoformat(),
        )

        await m.answer(
            f"✅ Задача «<b>{title}</b>» сохранена.\n"
            f"Дедлайн: <b>{deadline.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
            "Список активных задач — в кнопке <b>«📋 Мои задачи»</b>.",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )

    # ────────────────────────────────
    # /done 5 — старый способ закрыть задачу
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

        # логируем последнее действие закрытия
        save_last_action(
            chat_id=m.chat.id,
            user_id=m.from_user.id,
            action_type="close_task",
            task_id=task_id,
            completion_id=None,
        )

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
        completion_id = add_completion(task_id, user.id)

        # сохраняем последнее действие — отметка выполнения
        chat_id = callback_query.message.chat.id
        save_last_action(
            chat_id=chat_id,
            user_id=user.id,
            action_type="completion",
            task_id=task_id,
            completion_id=completion_id,
        )

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

        mark_done(task_id)# сохраняем последнее действие — закрытие задачи
        chat_id = callback_query.message.chat.id
        user_id = callback_query.from_user.id
        save_last_action(
            chat_id=chat_id,
            user_id=user_id,
            action_type="close_task",
            task_id=task_id,
            completion_id=None,
        )

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

        mark_done(task_id)

        save_last_action(
            chat_id=m.chat.id,
            user_id=m.from_user.id,
            action_type="close_task",
            task_id=task_id,
            completion_id=None,
        )

        await m.answer(
            f"🔒 Задача #{task_id} «{task['title']}» закрыта и больше не будет в списке.",
            reply_markup=main_menu(),
        )

    # ────────────────────────────────
    # /undo и кнопка «Отменить последнее»
    # ────────────────────────────────
        @dp.message_handler(commands=["undo"])
    @dp.message_handler(lambda m: m.text == "↩️ Отменить последнее")
    async def undo_last(m: types.Message, state: FSMContext):
        """
        1) Если сейчас пользователь в режиме ввода новой задачи (FSM),
           просто выходим из этого режима и ничего не сохраняем.
        2) Иначе откатываем последнее действие из last_actions.
        """
        # 1. Сначала проверяем, не висим ли мы в режиме ввода задачи
        current_state = await state.get_state()
        if current_state == TaskFSM.waiting_single_line.state:
            await state.finish()
            await m.answer(
                "Окей, отменяю ввод новой задачи. Ничего не сохранила 🙂",
                reply_markup=main_menu(),
            )
            return

        # 2. Обычная отмена последнего действия из таблицы last_actions
        action = get_last_action(m.chat.id)
        if not action:
            await m.answer(
                "Отменять пока нечего — последнее действие не найдено.",
                reply_markup=main_menu(),
            )
            return

        action_type = action["action_type"]
        task_id = action["task_id"]
        completion_id = action.get("completion_id")

        task = get_task(task_id)
        title = task["title"] if task else f"задача #{task_id}"

        if action_type == "add_task":
            # считаем, что "отмена добавления" = скрыть задачу
            mark_done(task_id)
            msg = f"↩️ Отменила добавление: «{title}». Задача скрыта из списка."
        elif action_type == "close_task":
            restore_task_status(task_id)
            msg = f"↩️ Отменила закрытие задачи: «{title}». Она снова активна."
        elif action_type == "completion":
            if completion_id is not None:
                delete_completion(completion_id)
                msg = f"↩️ Отменила отметку выполнения для задачи: «{title}»."
            else:
                msg = "Не получилось отменить отметку выполнения (нет id записи)."
        else:
            msg = "Неизвестный тип действия, отмена невозможна."

        clear_last_action(m.chat.id)

        await m.answer(msg, reply_markup=main_menu())
        
    # ────────────────────────────────
    # Отладочный хэндлер — всё, что не поймали другие
    # ────────────────────────────────
    @dp.message_handler()
    async def debug_fallback(m: types.Message):
        logger.info(
            "DEBUG MESSAGE: chat_id=%s type=%s from=%s text=%r",
            m.chat.id,
            m.chat.type,
            m.from_user.id if m.from_user else None,
            m.text,
        )


# ────────────────────────────────
# Вспомогательные функции для напоминаний
# ────────────────────────────────
def _shift_to_work_morning(date_obj):
    """
    Берём дату, возвращаем datetime в 09:00 утра.
    Если это суббота/воскресенье — сдвигаем на ближайший понедельник 09:00.
    """
    from datetime import date as _date  # локально, чтобы не путать импорты

    # если нам прилетел datetime — берём только дату
    if not isinstance(date_obj, _date):
        date_obj = date_obj.date()

    # 5 = суббота, 6 = воскресенье
    while date_obj.weekday() >= 5:
        date_obj += timedelta(days=1)

    return datetime.combine(date_obj, time(9, 0))

async def reminder_job(bot, task_id: int, chat_id: int, offset: int):
    """
    Джоба для APScheduler: перед отправкой проверяем,
    что задача ещё active.
    """
    from app.db import get_task  # локальный импорт, чтобы избежать циклов

    task = get_task(task_id)
    if not task:
        return

    # если задача уже закрыта — не напоминаем
    if task.get("status") != "active":
        return

    title = task.get("title", "без названия")

    texts = {
        3: f"⏳ Напоминание: через пару дней дедлайн по задаче: «{title}»",
        1: f"⚡ Напоминание: завтра дедлайн по задаче: «{title}»",
        0: f"🔥 Сегодня дедлайн по задаче: «{title}»",
    }

    text = texts.get(offset)
    if not text:
        return

    await bot.send_message(chat_id, text)


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
    """
    Планируем напоминания:
    - за 3 дня до дедлайна, в 09:00 (рабочий день)
    - за 1 день до дедлайна, в 09:00 (рабочий день)
    - в день дедлайна, в 09:00 (если это рабочий день,
      иначе перенос на ближайший понедельник)

    Напоминания отправляются только если задача всё ещё active.
    """
    # нормализуем deadline к datetime
    if isinstance(deadline, str):
        try:
            deadline_dt = datetime.fromisoformat(deadline)
        except ValueError:
            return
    else:
        deadline_dt = deadline

    for offset in (3, 1, 0):
        target_date = (deadline_dt - timedelta(days=offset)).date()

        # приводим к рабочему дню 09:00
        remind_dt = _shift_to_work_morning(target_date)

        # если это время уже прошло — не планируем
        if remind_dt <= datetime.now():
            continue

        scheduler.add_job(
            reminder_job,
            trigger="date",
            run_date=remind_dt,
            args=(dp.bot, task_id, chat_id, offset),
        )
