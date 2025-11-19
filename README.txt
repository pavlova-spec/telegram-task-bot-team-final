
Telegram Task Bot — Team Edition (Windows, no Docker)
=====================================================

Запуск:
  1) Распакуй архив и перейди в папку проекта
     cd C:\Users\Anastasia\Desktop\telegram-task-bot-team-final
  2) Создай .env на основе .env.example и вставь BOT_TOKEN, OWNER_ID
     copy .env.example .env
     notepad .env
  3) Виртуальное окружение и зависимости
     python -m venv venv
     venv\Scripts\activate
     pip install -r requirements.txt
  4) Запуск как пакет (важно!)
     python -m app.main

Что умеет бот:
  • Главное меню с кнопками
  • Создание задачи через диалог: Название → Календарь → Время
  • Напоминания: за 3 дня, за 1 день, в момент дедлайна (в тот же чат)
  • Список задач, отметка "Выполнено", удаление
  • Командный стиль с мотивирующими фразами

Заметки:
  • БД хранится в app/data.db (SQLite), создаётся автоматически
  • Работает на Python 3.10–3.12
