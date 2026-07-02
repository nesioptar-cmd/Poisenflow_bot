import os
import sqlite3
import logging
from datetime import datetime, timezone
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
API_TOKEN = os.environ.get("HF_API_TOKEN")
ACCOUNT_ID = os.environ.get("HF_ACCOUNT_ID")
HF_API_BASE = "https://api.huntflow.ru/v2"
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
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_settings ("
        "  chat_id INTEGER PRIMARY KEY,"
        "  status_change INTEGER DEFAULT 1,"
        "  overdue INTEGER DEFAULT 0"
        ")"
    )
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


def _get_status_change(chat_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT status_change FROM user_settings WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    conn.close()
    return row is None or row[0] == 1


def _get_settings(chat_id: int) -> tuple[int, int]:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT status_change, overdue FROM user_settings WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    conn.close()
    if row:
        return row[0], row[1]
    return 1, 0


def _set_settings(chat_id: int, status_change: int | None = None, overdue: int | None = None):
    conn = sqlite3.connect(str(DB_PATH))
    current = conn.execute(
        "SELECT status_change, overdue FROM user_settings WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    if current:
        sc = status_change if status_change is not None else current[0]
        ov = overdue if overdue is not None else current[1]
        conn.execute(
            "UPDATE user_settings SET status_change = ?, overdue = ? WHERE chat_id = ?",
            (sc, ov, chat_id),
        )
    else:
        conn.execute(
            "INSERT INTO user_settings (chat_id, status_change, overdue) VALUES (?, ?, ?)",
            (chat_id, status_change if status_change is not None else 1,
             overdue if overdue is not None else 0),
        )
    conn.commit()
    conn.close()


def _get_all_mappings() -> list[tuple[str, int]]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("SELECT email, chat_id FROM user_mappings").fetchall()
    conn.close()
    return rows


def _get_all_chat_ids_with_overdue() -> list[int]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("SELECT chat_id FROM user_settings WHERE overdue = 1").fetchall()
    conn.close()
    return [r[0] for r in rows]


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


# ─── Endpoints ─────────────────────────────────────────────────


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


@app.route("/api/sync-mappings", methods=["GET"])
def sync_mappings():
    return {"items": [{"email": e, "chat_id": c} for e, c in _get_all_mappings()]}


@app.route("/api/settings/<int:chat_id>", methods=["GET"])
def get_user_settings(chat_id: int):
    sc, ov = _get_settings(chat_id)
    return {"chat_id": chat_id, "status_change": sc, "overdue": ov}


@app.route("/api/settings/<int:chat_id>", methods=["POST"])
def set_user_settings(chat_id: int):
    data = request.get_json()
    sc = data.get("status_change")
    ov = data.get("overdue")
    if sc is not None:
        sc = int(sc)
    if ov is not None:
        ov = int(ov)
    _set_settings(chat_id, status_change=sc, overdue=ov)
    return {"status": "ok"}


@app.route("/api/overdue-notified", methods=["GET"])
def get_overdue_notified():
    aid = request.args.get("applicant_id", type=int)
    vid = request.args.get("vacancy_id", type=int)
    sid = request.args.get("status_id", type=int)
    if aid and vid and sid:
        return {"was_notified": _was_notified(aid, vid, sid)}
    return {"error": "missing params"}, 400


@app.route("/api/overdue-notified", methods=["POST"])
def mark_overdue_notified():
    data = request.get_json()
    aid = data.get("applicant_id")
    vid = data.get("vacancy_id")
    sid = data.get("status_id")
    if aid and vid and sid:
        _mark_notified(aid, vid, sid)
        return {"status": "ok"}
    return {"error": "missing params"}, 400


@app.route("/api/overdue-recipients", methods=["GET"])
def get_overdue_recipients():
    chat_ids = _get_all_chat_ids_with_overdue()
    return {"chat_ids": chat_ids}


# ─── Overdue Check (через внешний cron) ──────────────────────


def _hf_get(path: str) -> dict:
    import requests as rq
    resp = rq.get(f"{HF_API_BASE}{path}", headers={"Authorization": f"Bearer {API_TOKEN}"})
    resp.raise_for_status()
    return resp.json()


def _get_statuses() -> dict[int, dict]:
    data = _hf_get(f"/accounts/{ACCOUNT_ID}/vacancies/statuses")
    return {s["id"]: s for s in data["items"] if not s.get("removed")}


def _get_coworker_emails() -> dict[int, str]:
    data = _hf_get(f"/accounts/{ACCOUNT_ID}/coworkers")
    return {cw.get("member"): cw.get("email", "")
            for cw in data.get("items", []) if cw.get("member")}


def _get_all_applicants() -> list[dict]:
    items = []
    page = 1
    max_pages = 20
    while page <= max_pages:
        data = _hf_get(
            f"/accounts/{ACCOUNT_ID}/applicants?page={page}&count=30&order_by=-id"
        )
        items.extend(data.get("items", []))
        if page >= data.get("total_pages", 1):
            break
        page += 1
    return items


def _get_applicant_logs(applicant_id: int) -> list[dict]:
    data = _hf_get(
        f"/accounts/{ACCOUNT_ID}/applicants/{applicant_id}/logs?page=1&count=5"
    )
    return data.get("items", [])


def _get_vacancy_name(vacancy_id: int) -> str:
    try:
        data = _hf_get(f"/accounts/{ACCOUNT_ID}/vacancies/{vacancy_id}")
        return data.get("position", f"ID {vacancy_id}")
    except Exception:
        return f"ID {vacancy_id}"


def _run_overdue_check():
    if not API_TOKEN or not ACCOUNT_ID:
        return {"error": "HF_API_TOKEN или HF_ACCOUNT_ID не настроены"}

    import requests as rq

    statuses = _get_statuses()
    coworker_emails = _get_coworker_emails()
    applicants = _get_all_applicants()
    notified_total = 0

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
        for log in _get_applicant_logs(app["id"]):
            if log.get("type") == "STATUS" and log.get("status") == status_id:
                ai = log.get("account_info", {}) or {}
                member_id = ai.get("member")
                if member_id:
                    recruiter_email = coworker_emails.get(member_id)
                break

        app_name = " ".join(filter(None, [
            app.get("first_name"), app.get("last_name")
        ])) or f"ID {app['id']}"
        vacancy_name = _get_vacancy_name(vacancy_id)
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
                    rq.post(TELEGRAM_API_URL, json={
                        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
                    })
                    notified.add(chat_id)
                except Exception:
                    pass

        if not notified:
            for chat_id in _get_all_chat_ids_with_overdue():
                if chat_id not in notified:
                    try:
                        rq.post(TELEGRAM_API_URL, json={
                            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
                        })
                        notified.add(chat_id)
                    except Exception:
                        pass

        if notified:
            _mark_notified(app["id"], vacancy_id, status_id)
            notified_total += len(notified)

    return {"status": "ok", "notifications": notified_total}


@app.route("/api/check-overdue", methods=["GET"])
def handle_check_overdue():
    result = _run_overdue_check()
    if "error" in result:
        return result, 400
    return result


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

    # Проверка настройки пользователя
    if not _get_status_change(tg_chat_id):
        logging.info("У пользователя %s отключены уведомления о смене этапа", author_email)
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
