import os
import sqlite3
import logging
from collections import defaultdict
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
HF_WEB = f"https://huntflow.ru/account/{ACCOUNT_ID}"
DB_PATH = Path(__file__).parent / "mapping.db"
HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}

if not all([BOT_TOKEN, API_TOKEN, ACCOUNT_ID]):
    raise RuntimeError("Задайте BOT_TOKEN, HF_API_TOKEN и HF_ACCOUNT_ID в .env")


# ─── БД ────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS overdue_log ("
        "  applicant_id INTEGER,"
        "  vacancy_id INTEGER,"
        "  status_id INTEGER,"
        "  sent_at TEXT DEFAULT (datetime('now')),"
        "  PRIMARY KEY (applicant_id, vacancy_id, status_id)"
        ")"
    )
    conn.commit()
    conn.close()


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
    return {s["id"]: s for s in data["items"] if not s.get("removed")}


def get_coworker_emails() -> dict[int, str]:
    data = hf_get(f"/accounts/{ACCOUNT_ID}/coworkers")
    return {cw.get("member"): cw.get("email", "")
            for cw in data.get("items", []) if cw.get("member")}


def get_all_applicants() -> list[dict]:
    items = []
    page = 1
    max_pages = 20
    while page <= max_pages:
        data = hf_get(f"/accounts/{ACCOUNT_ID}/applicants?page={page}&count=30&order_by=-id")
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
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
    )


# ─── ЛОГИКА ─────────────────────────────────────────────────────

def check_overdue():
    statuses = get_statuses()
    coworker_emails = get_coworker_emails()
    applicants = get_all_applicants()

    logging.info("Проверка %d кандидатов...", len(applicants))
    checked = 0

    # Собираем просрочки: {(vacancy_id, recruiter_email): [item, ...]}
    overdue_by_vacancy = defaultdict(list)

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
        if changed.tzinfo is not None:
            now = datetime.now(timezone.utc).astimezone()
            days_on = (now - changed).days
        else:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            days_on = (now - changed).days

        if days_on <= max_days:
            continue

        if _was_notified(app["id"], vacancy_id, status_id):
            continue

        recruiter_email = None
        for log in get_applicant_logs(app["id"]):
            if log.get("type") == "STATUS" and log.get("status") == status_id:
                ai = log.get("account_info", {}) or {}
                member_id = ai.get("member")
                if member_id:
                    recruiter_email = coworker_emails.get(member_id)
                break

        app_name = " ".join(filter(None, [app.get("first_name"), app.get("last_name")])) or f"ID {app['id']}"
        stage_name = status_info.get("name", f"ID {status_id}")

        overdue_by_vacancy[(vacancy_id, recruiter_email)].append({
            "applicant_id": app["id"],
            "status_id": status_id,
            "name": app_name,
            "stage": stage_name,
            "days_on": days_on,
            "max_days": max_days,
        })

        checked += 1
        if checked % 100 == 0:
            logging.info("Обработано %d/%d", checked, len(applicants))

    # ── Отправка ──
    notified_total = 0
    vacancy_name_cache = {}

    for (vacancy_id, recruiter_email), items in overdue_by_vacancy.items():
        if vacancy_id not in vacancy_name_cache:
            vacancy_name_cache[vacancy_id] = get_vacancy_name(vacancy_id)
        vname = vacancy_name_cache[vacancy_id]

        parts = [f"⏰ <b>Просрочки</b>\n💼 <b>{vname}</b> ({len(items)})"]
        for it in items:
            parts.append(
                f"👤 {it['name']}\n"
                f"   🎯 {it['stage']} — {it['days_on']}/{it['max_days']} дн."
            )
        parts.append(f'\n<a href="{HF_WEB}/vacancy/{vacancy_id}/">🔗 Открыть вакансию</a>')
        text = "\n\n".join(parts)

        notified = set()

        if recruiter_email:
            chat_id = _get_chat_id(recruiter_email)
            if chat_id and chat_id not in notified:
                try:
                    send_telegram(chat_id, text)
                    notified.add(chat_id)
                    logging.info("Рекрутер %s уведомлён (%d просрочек)", recruiter_email, len(items))
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
            for it in items:
                _mark_notified(it["applicant_id"], vacancy_id, it["status_id"])
            notified_total += len(notified)

    logging.info("Проверка завершена, отправлено %d уведомлений", notified_total)


if __name__ == "__main__":
    init_db()
    check_overdue()
