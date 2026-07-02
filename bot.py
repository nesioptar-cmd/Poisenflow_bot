import logging

import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import settings
from database import save_mapping, get_all_mappings

bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher()


class AuthStates(StatesGroup):
    waiting_for_email = State()


async def sync_to_cloud():
    if not settings.PYTHONANYWHERE_URL:
        return
    mappings = await get_all_mappings()
    async with httpx.AsyncClient() as client:
        for email, chat_id in mappings:
            try:
                await client.post(
                    f"{settings.PYTHONANYWHERE_URL}/api/register",
                    json={"email": email, "chat_id": chat_id},
                )
            except httpx.RequestError as e:
                logging.warning("Ошибка синхронизации %s: %s", email, e)
    logging.info("Синхронизировано %d записей с облаком", len(mappings))


@dp.startup()
async def on_startup():
    logging.info("Запуск бота...")
    await sync_to_cloud()


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer(
        "👋 Привет! Я бот уведомлений Хантфлоу.\n"
        "Пожалуйста, напиши мне свой **рабочий email**, "
        "под которым ты зарегистрирован в Хантфлоу:"
    )
    await state.set_state(AuthStates.waiting_for_email)


@dp.message(AuthStates.waiting_for_email)
async def process_email(message: types.Message, state: FSMContext):
    email = message.text.strip().lower()
    chat_id = message.chat.id

    await save_mapping(email, chat_id)

    if settings.PYTHONANYWHERE_URL:
        async with httpx.AsyncClient() as client:
            try:
                await client.post(
                    f"{settings.PYTHONANYWHERE_URL}/api/register",
                    json={"email": email, "chat_id": chat_id},
                )
            except httpx.RequestError as e:
                logging.warning("Ошибка синхронизации: %s", e)

    logging.info("Сохранено: %s -> %s", email, chat_id)

    await message.answer(
        f"✅ Успешно привязано!\n"
        f"Почта: `{email}`\n"
        f"Telegram ID: `{chat_id}`\n\n"
        f"Теперь, когда вы переведёте кандидата в Хантфлоу, "
        f"сюда придёт уведомление."
    )
    await state.clear()


async def main():
    logging.basicConfig(level=logging.INFO)
    await bot.delete_webhook()
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
