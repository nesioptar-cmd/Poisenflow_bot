"""Скрипт проверки просрочек на этапах.

Запускается на PythonAnywhere по расписанию (Scheduled Tasks).
Использует ту же SQLite БД, что и webhook_app.py.
"""

import os
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_TOKEN = os.getenv("HF_API_TOKEN")
ACCOUNT_ID = os.getenv("HF_ACCOUNT_ID")

API_BASE = "https://api.huntflow.ru/v2"
DB_PATH = Path(__file__).parent / "mapping.db"
HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}

if not all([BOT_TOKEN, API_TOKEN, ACCOUNT_ID]):
    raise RuntimeError("Задайте BOT_TOKEN, HF_API_TOKEN и HF_ACCOUNT_ID в .env")


# ─── БД ────────────────────────────────────────────────────────

def _was_notified(applicant_id: int, vacancy_id: int, status_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT 1 FROM overdue_log WHERE applicant_id=? AND vacancy_id=? AND status_id=?",
        (applicant_id, vacancy_id, status_id),
    ).fetchone()
    conn.close()
    return row is not None


def _mark_notified(applicant_id: int, vacancy_id: int, status_id: int):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT OR IGNORE INTO overdue_log (applicant_id, vacancy_id, status_id) VALUES (?, ?, ?)",
        (applicant_id, vacancy_id, status_id),
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


def _get_overdue_chat_ids() -> list[int]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("SELECT chat_id FROM user_settings WHERE overdue = 1").fetchall()
    conn.close()
    return [r[0] for r in rows]


# ─── HF API ────────────────────────────────────────────────────

def hf_get(path: str) -> dict:
    resp = requests.get(f"{API_BASE}{path}", headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


def get_statuses() -> dict[int, dict]:
    data = hf_get(f"/accounts/{ACCOUNT_ID}/vacancies/statuses")
    result = {}
    for s in data["items"]:
        if not s.get("removed"):
            result[s["id"]] = s
    return result


def get_coworker_emails() -> dict[int, str]:
    data = hf_get(f"/accounts/{ACCOUNT_ID}/coworkers")
    return {cw.get("member"): cw.get("email", "") for cw in data.get("items", [])
            if cw.get("member")}


def get_all_applicants() -> list[dict]:
    items = []
    page = 1
    while True:
        data = hf_get(f"/accounts/{ACCOUNT_ID}/applicants?page={page}&count=100&order_by=-id")
        items.extend(data.get("items", []))
        if page >= data.get("total_pages", 1):
            break
        page += 1
    return items


def get_applicant_logs(applicant_id: int) -> list[dict]:
    data = hf_get(f"/accounts/{ACCOUNT_ID}/applicants/{applicant_id}/logs?page=1&count=5")
    return data.get("items", [])


def get_vacancy_name(vacancy_id: int) -> str:
    try:
        data = hf_get(f"/accounts/{ACCOUNT_ID}/vacancies/{vacancy_id}")
        return data.get("position", f"ID {vacancy_id}")
    except Exception:
        return f"ID {vacancy_id}"


def send_telegram(chat_id: int, text: str):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
    )


# ─── ЛОГИКА ─────────────────────────────────────────────────────

def check_overdue():
    statuses = get_statuses()
    coworker_emails = get_coworker_emails()
    applicants = get_all_applicants()

    logging.info("Проверка %d кандидатов...", len(applicants))

    for app in applicants:
        links = app.get("links", [])
        active = [l for l in links if l.get("rejection_reason") is None]
        if not active:
            continue

        link = active[0]
        status_id = link.get("status")
        vacancy_id = link.get("vacancy")
        changed_str = link.get("changed")

        if not status_id or not changed_str:
            continue

        status_info = statuses.get(status_id)
        if not status_info:
            continue

        max_days = status_info.get("stay_duration")
        if max_days is None:
            continue

        changed = datetime.fromisoformat(changed_str)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        days_on = (now - changed).days

        if days_on <= max_days:
            continue

        if _was_notified(app["id"], vacancy_id, status_id):
            continue

        # ── рекрутер ──
        recruiter_email = None
        for log in get_applicant_logs(app["id"]):
            if log.get("type") == "STATUS" and log.get("status") == status_id:
                ai = log.get("account_info", {}) or {}
                member_id = ai.get("member")
                if member_id:
                    recruiter_email = coworker_emails.get(member_id)
                break

        app_name = " ".join(filter(None, [app.get("first_name"), app.get("last_name")])) or f"ID {app['id']}"
        vacancy_name = get_vacancy_name(vacancy_id)
        stage_name = status_info.get("name", f"ID {status_id}")

        text = (
            f"⏰ <b>Просрочка!</b>\n\n"
            f"👤 <b>Кандидат:</b> {app_name}\n"
            f"💼 <b>Вакансия:</b> {vacancy_name}\n"
            f"🎯 <b>Этап:</b> {stage_name}\n"
            f"⏳ <b>Просрочено:</b> {days_on} дн. (максимум {max_days})"
        )

        notified = set()

        if recruiter_email:
            chat_id = _get_chat_id(recruiter_email)
            if chat_id and chat_id not in notified:
                try:
                    send_telegram(chat_id, text)
                    notified.add(chat_id)
                    logging.info("Рекрутер %s уведомлён", recruiter_email)
                except Exception as e:
                    logging.error("Ошибка рекрутеру %s: %s", recruiter_email, e)

        if not notified:
            for chat_id in _get_overdue_chat_ids():
                if chat_id not in notified:
                    try:
                        send_telegram(chat_id, text)
                        notified.add(chat_id)
                    except Exception as e:
                        logging.error("Ошибка %s: %s", chat_id, e)

        if notified:
            _mark_notified(app["id"], vacancy_id, status_id)

    logging.info("Проверка завершена")


if __name__ == "__main__":
    check_overdue()
