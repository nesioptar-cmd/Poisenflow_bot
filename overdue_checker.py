import os
import sqlite3
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import holidays

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

account_info = requests.get(f"{API_BASE}/accounts/{ACCOUNT_ID}", headers=HEADERS).json()
HF_WEB = f"https://huntflow.ru/app/my/{account_info['nick']}"

if not all([BOT_TOKEN, API_TOKEN, ACCOUNT_ID]):
    raise RuntimeError("Задайте BOT_TOKEN, HF_API_TOKEN и HF_ACCOUNT_ID в .env")

ru_holidays = holidays.country_holidays("RU")


def count_working_days(start: datetime, end: datetime) -> int:
    days = 0
    current = start
    while current.date() < end.date():
        if current.weekday() < 5 and current not in ru_holidays:
            days += 1
        current += timedelta(days=1)
    return days


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


def _get_chat_id(email: str) -> int | None:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT chat_id FROM user_mappings WHERE email = ?", (email,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


# ─── HF API ────────────────────────────────────────────────────

def hf_get(path: str) -> dict:
    resp = requests.get(f"{API_BASE}{path}", headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


def get_statuses() -> dict[int, dict]:
    data = hf_get(f"/accounts/{ACCOUNT_ID}/vacancies/statuses")
    return {s["id"]: s for s in data["items"] if not s.get("removed")}


def get_coworkers() -> dict[int, str]:
    data = hf_get(f"/accounts/{ACCOUNT_ID}/coworkers")
    return {cw["member"]: cw.get("email", "")
            for cw in data.get("items", []) if cw.get("member") and cw.get("email")}


def get_open_vacancies() -> list[dict]:
    items = []
    page = 1
    while True:
        data = hf_get(f"/accounts/{ACCOUNT_ID}/vacancies?state=OPEN&page={page}&count=30")
        items.extend(data.get("items", []))
        if page >= data.get("total_pages", 1):
            break
        page += 1
    return items


def get_vacancy_applicants(vacancy_id: int) -> list[dict]:
    items = []
    page = 1
    while True:
        data = hf_get(f"/accounts/{ACCOUNT_ID}/applicants?vacancy={vacancy_id}&page={page}&count=30")
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
    coworkers = get_coworkers()
    vacancies = get_open_vacancies()

    logging.info("Открытых вакансий: %d", len(vacancies))

    # recruiter_email → {vacancy_id → [overdue_items]}
    overdue_by_recruiter = defaultdict(lambda: defaultdict(list))
    total_checked = 0

    for vac in vacancies:
        vacancy_id = vac["id"]
        vname = vac.get("position", f"ID {vacancy_id}")
        applicants = get_vacancy_applicants(vacancy_id)

        for app in applicants:
            total_checked += 1
            # Берём только ссылки на ЭТУ вакансию, сортируем по ID (свежие выше)
            vacancy_links = sorted(
                [l for l in app.get("links", []) if l.get("vacancy") == vacancy_id],
                key=lambda l: l.get("id", 0), reverse=True
            )
            if not vacancy_links:
                continue
            # Если последнее действие на этой вакансии — отказ, кандидат не в работе
            if vacancy_links[0].get("rejection_reason") is not None:
                continue
            link = vacancy_links[0]
            status_id = link.get("status")
            changed_str = link.get("changed")

            if not status_id or not changed_str:
                continue

            status_info = statuses.get(status_id)
            if not status_info:
                continue

            if status_id in (132570, 134331):
                continue

            max_days = status_info.get("stay_duration")
            if max_days is None:
                continue

            changed = datetime.fromisoformat(changed_str)
            if changed.tzinfo is not None:
                now = datetime.now(timezone.utc).astimezone()
            else:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
            days_on = count_working_days(changed, now)

            if days_on <= max_days:
                continue

            # Проверяем логи: был ли отказ на этой вакансии
            logs = get_applicant_logs(app["id"])
            has_rejection = any(
                l.get("type") == "STATUS" and l.get("status") == 132570
                and l.get("rejection_reason") is not None
                and (l.get("vacancy") or l.get("vacancy_id")) == vacancy_id
                for l in logs
            )
            if has_rejection:
                continue

            recruiter_email = None
            for log in logs:
                if log.get("type") == "STATUS" and log.get("status") == status_id:
                    ai = log.get("account_info", {}) or {}
                    cw_id = ai.get("id")
                    if cw_id:
                        recruiter_email = coworkers.get(cw_id)
                    break

            app_name = " ".join(filter(None, [app.get("first_name"), app.get("last_name")])) or f"ID {app['id']}"
            stage_name = status_info.get("name", f"ID {status_id}")

            overdue_by_recruiter[recruiter_email][vacancy_id].append({
                "applicant_id": app["id"],
                "status_id": status_id,
                "name": app_name,
                "stage": stage_name,
                "days_on": days_on,
                "max_days": max_days,
            })

        logging.info("Вакансия %s: %d кандидатов", vname, len(applicants))

    logging.info("Проверено %d кандидатов на %d вакансиях", total_checked, len(vacancies))

    total_overdue = sum(len(v) for v in overdue_by_recruiter.values())
    logging.info("Найдено просрочек: %d, из них без рекрутера: %d",
                 total_overdue, len(overdue_by_recruiter.get(None, [])))

    # ── Отправка: только тем, кто авторизован в боте ──
    notified_total = 0

    for recruiter_email, vacancies_dict in overdue_by_recruiter.items():
        if not recruiter_email:
            logging.info("Пропущен кандидат без известного рекрутера")
            continue

        chat_id = _get_chat_id(recruiter_email)
        if not chat_id:
            logging.info("Рекрутер %s не авторизован в боте — пропущен", recruiter_email)
            continue

        parts = ["⏰ <b>Просрочки по вашим кандидатам</b>"]
        for vacancy_id, items in vacancies_dict.items():
            vname = get_vacancy_name(vacancy_id)
            total_days = sum(it["days_on"] - it["max_days"] for it in items)
            parts.append(
                f"\n💼 <b>{vname}</b> — {total_days} дней просрочки"
            )
            parts.append(f'<a href="{HF_WEB}/vacancy/{vacancy_id}/">🔗 Открыть вакансию</a>')
        text = "\n\n".join(parts)

        try:
            send_telegram(chat_id, text)
            notified_total += 1
            logging.info("Рекрутер %s уведомлён (%d вакансий)", recruiter_email, len(vacancies_dict))
        except Exception as e:
            logging.error("Ошибка рекрутеру %s: %s", recruiter_email, e)

    logging.info("Проверка завершена, отправлено %d уведомлений", notified_total)


if __name__ == "__main__":
    init_db()
    check_overdue()
