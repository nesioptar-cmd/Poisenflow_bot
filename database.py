import asyncio
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "mapping.db"


def _init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_mappings ("
        "  email TEXT PRIMARY KEY,"
        "  chat_id INTEGER NOT NULL,"
        "  created_at TEXT DEFAULT (datetime('now'))"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_settings ("
        "  chat_id INTEGER PRIMARY KEY,"
        "  status_change INTEGER DEFAULT 1,"
        "  overdue INTEGER DEFAULT 0"
        ")"
    )
    for col in ["check_frequency", "last_overdue_notified"]:
        try:
            conn.execute(f"ALTER TABLE user_settings ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def _save_mapping(email: str, chat_id: int):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT OR REPLACE INTO user_mappings (email, chat_id) VALUES (?, ?)",
        (email, chat_id),
    )
    conn.commit()
    conn.close()


def _get_chat_id(email: str) -> int | None:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT chat_id FROM user_mappings WHERE email = ?", (email,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


async def init_db():
    await asyncio.to_thread(_init_db)


async def save_mapping(email: str, chat_id: int):
    await asyncio.to_thread(_save_mapping, email, chat_id)


async def get_chat_id(email: str) -> int | None:
    return await asyncio.to_thread(_get_chat_id, email)


async def get_all_mappings() -> list[tuple[str, int]]:
    return await asyncio.to_thread(_get_all_mappings)


def _get_all_mappings() -> list[tuple[str, int]]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT email, chat_id FROM user_mappings"
    ).fetchall()
    conn.close()
    return rows


def _get_settings(chat_id: int) -> tuple[int, int, str]:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT status_change, overdue, check_frequency FROM user_settings WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    conn.close()
    if row:
        return row[0], row[1], row[2] or "hourly"
    return 1, 0, "hourly"


def _set_settings(chat_id: int, status_change: int | None = None, overdue: int | None = None, check_frequency: str | None = None):
    conn = sqlite3.connect(str(DB_PATH))
    current = conn.execute(
        "SELECT status_change, overdue, check_frequency FROM user_settings WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    if current:
        sc = status_change if status_change is not None else current[0]
        ov = overdue if overdue is not None else current[1]
        cf = check_frequency if check_frequency is not None else (current[2] or "hourly")
        conn.execute(
            "UPDATE user_settings SET status_change = ?, overdue = ?, check_frequency = ? WHERE chat_id = ?",
            (sc, ov, cf, chat_id),
        )
    else:
        conn.execute(
            "INSERT INTO user_settings (chat_id, status_change, overdue, check_frequency) VALUES (?, ?, ?, ?)",
            (chat_id, status_change if status_change is not None else 1,
             overdue if overdue is not None else 0,
             check_frequency or "hourly"),
        )
    conn.commit()
    conn.close()


def _update_last_notified(chat_id: int):
    from datetime import datetime
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE user_settings SET last_overdue_notified = ? WHERE chat_id = ?",
        (datetime.now().isoformat(), chat_id),
    )
    conn.commit()
    conn.close()


async def get_settings(chat_id: int) -> tuple[int, int, str]:
    return await asyncio.to_thread(_get_settings, chat_id)


async def set_settings(chat_id: int, status_change: int | None = None, overdue: int | None = None, check_frequency: str | None = None):
    await asyncio.to_thread(_set_settings, chat_id, status_change, overdue, check_frequency)


def get_last_notified(chat_id: int) -> str | None:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT last_overdue_notified FROM user_settings WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def update_last_notified(chat_id: int):
    _update_last_notified(chat_id)


_init_db()
