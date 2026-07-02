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
