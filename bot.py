import logging

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import settings
from database import save_mapping

bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher()


class AuthStates(StatesGroup):
    waiting_for_email = State()


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

    logging.info("Сохранено: %s -> %s", email, chat_id)

    await message.answer(
        f"✅ Успешно привязано!\n"
        f"Почта: `{email}`\n"
        f"Telegram ID: `{chat_id}`\n\n"
        f"Теперь, когда вы переведёте кандидата в Хантфлоу, "
        f"сюда придёт уведомление."
    )
    await state.clear()
