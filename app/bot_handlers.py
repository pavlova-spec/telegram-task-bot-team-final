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
