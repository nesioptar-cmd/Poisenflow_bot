import logging

import httpx
from fastapi import APIRouter, Request, Response, status

from config import settings
from database import get_chat_id

router = APIRouter()

TELEGRAM_API_URL = f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage"


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/huntflow-webhook")
async def handle_huntflow_webhook(request: Request):
    if request.headers.get("X-Huntflow-Event") == "PING":
        logging.info("Получен PING от Хантфлоу")
        return Response(status_code=status.HTTP_200_OK)

    payload = await request.json()
    meta = payload.get("meta", {}) or {}
    event_data = payload.get("event", {}) or {}
    applicant_log = event_data.get("applicant_log", {}) or {}

    log_type = applicant_log.get("type", "UNKNOWN")
    author_email = (meta.get("author") or {}).get("email", "").lower()

    logging.info(
        "Вебхук: log_type=%s author=%s",
        log_type, author_email or "(нет email)",
    )

    if log_type != "STATUS":
        logging.info("Пропущен log_type=%s (ожидается STATUS)", log_type)
        return Response(status_code=status.HTTP_200_OK)

    if not author_email:
        logging.warning("В вебхуке нет author.email")
        return Response(status_code=status.HTTP_200_OK)

    tg_chat_id = await get_chat_id(author_email)
    if not tg_chat_id:
        logging.info("Пользователь %s не зарегистрирован в боте", author_email)
        return Response(status_code=status.HTTP_200_OK)

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

    async with httpx.AsyncClient() as client:
        tg_payload = {
            "chat_id": tg_chat_id,
            "text": message_text,
            "parse_mode": "HTML",
        }
        resp = await client.post(TELEGRAM_API_URL, json=tg_payload)
        if resp.status_code == 200:
            logging.info("Уведомление отправлено %s (chat_id=%s)", author_email, tg_chat_id)
        else:
            logging.error("Ошибка Telegram: %s", resp.text)

    return Response(status_code=status.HTTP_200_OK)
