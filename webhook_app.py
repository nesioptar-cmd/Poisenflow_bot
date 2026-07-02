import os
import sqlite3
import logging
from pathlib import Path
from flask import Flask, request

logging.basicConfig(level=logging.INFO)

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. Создайте .env файл в папке проекта с содержимым: BOT_TOKEN=..."
    )
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
DB_PATH = Path(__file__).parent / "mapping.db"

app = Flask(__name__)


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


@app.route("/health")
def health():
    return {"status": "ok"}


@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json()
    email = (data.get("email") or "").strip().lower()
    chat_id = data.get("chat_id")
    if not email or not chat_id:
        return {"error": "email and chat_id required"}, 400
    _save_mapping(email, chat_id)
    logging.info("Синхронизирован: %s -> %s", email, chat_id)
    return {"status": "ok"}


@app.route("/huntflow-webhook", methods=["POST"])
def handle_huntflow_webhook():
    if request.headers.get("X-Huntflow-Event") == "PING":
        logging.info("Получен PING от Хантфлоу")
        return "", 200

    payload = request.get_json()
    meta = payload.get("meta", {}) or {}
    event_data = payload.get("event", {}) or {}
    applicant_log = event_data.get("applicant_log", {}) or {}

    log_type = applicant_log.get("type", "UNKNOWN")
    author_email = (meta.get("author") or {}).get("email", "").lower()

    logging.info("Вебхук: log_type=%s author=%s", log_type, author_email or "(нет email)")

    if log_type != "STATUS":
        logging.info("Пропущен log_type=%s (ожидается STATUS)", log_type)
        return "", 200

    if not author_email:
        logging.warning("В вебхуке нет author.email")
        return "", 200

    tg_chat_id = _get_chat_id(author_email)
    if not tg_chat_id:
        logging.info("Пользователь %s не зарегистрирован в боте", author_email)
        return "", 200

    applicant = event_data.get("applicant", {}) or {}
    vacancy = applicant_log.get("vacancy", {}) or {}
    new_status = applicant_log.get("status", {}) or {}

    first = applicant.get("first_name", "")
    last = applicant.get("last_name", "")
    applicant_name = f"{first} {last}".strip() or "Неизвестный кандидат"
    vacancy_name = vacancy.get("position", "Без названия")
    status_name = new_status.get("name", "Неизвестный этап")

    message_text = (
        f"🔄 <b>Смена этапа кандидата!</b>\n\n"
        f"👤 <b>Кандидат:</b> {applicant_name}\n"
        f"💼 <b>Вакансия:</b> {vacancy_name}\n"
        f"🎯 <b>Новый этап:</b> 👉 {status_name} 👈"
    )

    import requests

    resp = requests.post(
        TELEGRAM_API_URL,
        json={"chat_id": tg_chat_id, "text": message_text, "parse_mode": "HTML"},
    )
    if resp.status_code == 200:
        logging.info("Уведомление отправлено %s (chat_id=%s)", author_email, tg_chat_id)
    else:
        logging.error("Ошибка Telegram: %s", resp.text)

    return "", 200


_init_db()
