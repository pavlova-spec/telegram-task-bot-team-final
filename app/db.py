import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "bot.db")


def _dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = _dict_factory
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # Основная таблица задач
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            creator_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            deadline_ts TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        )
        """
    )

    # Таблица отметок выполнения задач
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS task_completions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            completed_at TEXT NOT NULL,
            UNIQUE(task_id, user_id)
        )
        """
    )

    # Таблица последних действий (для отмены)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS last_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,       -- 'add_task', 'close_task', 'completion'
            task_id INTEGER NOT NULL,
            completion_id INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()


def add_task(chat_id: int, title: str, deadline: datetime, creator_id: int) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO tasks (chat_id, creator_id, title, deadline_ts, status, created_at)
        VALUES (?, ?, ?, ?, 'active', ?)
        """,
        (
            chat_id,
            creator_id,
            title,
            deadline.isoformat(),
            datetime.utcnow().isoformat(),
        ),
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


def get_tasks(chat_id: int):
    """
    Вернуть активные задачи для чата.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM tasks
        WHERE chat_id = ? AND status = 'active'
        ORDER BY datetime(deadline_ts) ASC
        """,
        (chat_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def mark_done(task_id: int):
    """
    Пометить задачу как завершённую (больше не показывается в списке).
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE tasks SET status='done' WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def get_active_tasks():
    """
    Все активные задачи (для пересоздания напоминаний на старте бота).
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE status='active'")
    rows = cur.fetchall()
    conn.close()
    return rows


# ─────────────────────────────────────────────
# Отметки выполнения задач
# ─────────────────────────────────────────────

def add_completion(task_id: int, user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO task_completions (task_id, user_id, completed_at)
        VALUES (?, ?, ?)
        """,
        (task_id, user_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    completion_id = cur.lastrowid
    conn.close()
    return completion_id


def get_task(task_id: int):
    """
    Получить одну задачу по id.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_task_completions(task_id: int):
    """
    Получить всех пользователей, отметивших задачу выполненной.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM task_completions
        WHERE task_id = ?
        ORDER BY datetime(completed_at) ASC
        """,
        (task_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ─────────────────────────────────────────────
# UNDO: последние действия
# ─────────────────────────────────────────────

def save_last_action(
    chat_id: int,
    user_id: int,
    action_type: str,
    task_id: int,
    completion_id: int | None = None,
):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO last_actions (chat_id, user_id, action_type, task_id, completion_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (chat_id, user_id, action_type, task_id, completion_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_last_action(chat_id: int, user_id: int | None = None):
    """
    Получить последнее действие.

    Если передан user_id — берём последнюю запись
    именно этого пользователя в данном чате.
    Если нет — работаем по-старому, только по chat_id.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if user_id is None:
        cur.execute(
            """
            SELECT *
            FROM last_actions
            WHERE chat_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (chat_id,),
        )
    else:
        cur.execute(
            """
            SELECT *
            FROM last_actions
            WHERE chat_id = ? AND user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (chat_id, user_id),
        )

    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def clear_last_action(chat_id: int, user_id: int | None = None):
    """
    Очистить последнюю запись.

    Если user_id не указан — удаляем все записи по чату (старое поведение).
    Если указан — чистим только действия конкретного пользователя.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if user_id is None:
        cur.execute("DELETE FROM last_actions WHERE chat_id = ?", (chat_id,))
    else:
        cur.execute(
            "DELETE FROM last_actions WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )

    conn.commit()
    conn.close()


def restore_task_status(task_id: int):
    """
    Возвращаем задачу в статус 'active'.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE tasks SET status = 'active' WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def delete_completion(completion_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM task_completions WHERE id = ?", (completion_id,))
    conn.commit()
    conn.close()
