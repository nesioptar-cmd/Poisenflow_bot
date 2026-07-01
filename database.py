import logging

from databases import Database

from config import settings

DATABASE_URL = settings.DATABASE_URL
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

database = Database(DATABASE_URL)
_is_sqlite = DATABASE_URL.startswith("sqlite")


async def init_db():
    await database.connect()
    await database.execute(
        """CREATE TABLE IF NOT EXISTS user_mappings (
            email TEXT PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    logging.info("База данных инициализирована: %s", DATABASE_URL)


async def save_mapping(email: str, chat_id: int):
    if _is_sqlite:
        query = (
            "INSERT OR REPLACE INTO user_mappings (email, chat_id) "
            "VALUES (:email, :chat_id)"
        )
    else:
        query = (
            "INSERT INTO user_mappings (email, chat_id) VALUES (:email, :chat_id) "
            "ON CONFLICT (email) DO UPDATE SET chat_id = EXCLUDED.chat_id"
        )
    await database.execute(query, {"email": email, "chat_id": chat_id})


async def get_chat_id(email: str) -> int | None:
    query = "SELECT chat_id FROM user_mappings WHERE email = :email"
    row = await database.fetch_one(query, {"email": email})
    return row[0] if row else None
