import logging

import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database import save_mapping, get_all_mappings, get_settings, set_settings

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


FREQ_LABELS = {"hourly": "Каждый час", "3x_day": "3 раза в день", "daily": "Раз в день"}


def settings_text(sc: int, ov: int, freq: str) -> str:
    return (
        "⚙️ <b>Настройки уведомлений</b>\n\n"
        f"🔄 Смена этапа: {'✅ Вкл' if sc else '❌ Выкл'}\n"
        f"⏰ Просрочки: {'✅ Вкл' if ov else '❌ Выкл'}\n"
        f"📡 Частота проверки: {FREQ_LABELS.get(freq, freq)}\n\n"
        "Нажмите на кнопку, чтобы изменить:"
    )


def settings_kb(chat_id: int, sc: int, ov: int, freq: str) -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text=f"🔄 Смена этапа {'✅' if sc else '❌'}",
        callback_data=f"tog_sc_{chat_id}",
    )
    b.button(
        text=f"⏰ Просрочки {'✅' if ov else '❌'}",
        callback_data=f"tog_ov_{chat_id}",
    )
    for key, label in FREQ_LABELS.items():
        mark = "✅" if key == freq else "⚪️"
        b.button(text=f"{mark} {label}", callback_data=f"freq_{key}_{chat_id}")
    b.adjust(1)
    return b.as_markup()


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

    sc, ov, freq = await get_settings(chat_id)
    await message.answer(
        f"✅ Успешно привязано!\nПочта: `{email}`",
    )
    await message.answer(
        settings_text(sc, ov, freq),
        reply_markup=settings_kb(chat_id, sc, ov, freq),
        parse_mode="HTML",
    )
    await state.clear()


@dp.message(Command("settings"))
async def cmd_settings(message: types.Message):
    chat_id = message.chat.id
    sc, ov, freq = await get_settings(chat_id)
    await message.answer(
        settings_text(sc, ov, freq),
        reply_markup=settings_kb(chat_id, sc, ov, freq),
        parse_mode="HTML",
    )


@dp.callback_query(lambda c: c.data.startswith(("tog_", "freq_")))
async def toggle_setting(callback: types.CallbackQuery):
    # format: {prefix}_{value}_{chat_id}, chat_id always after last _
    chat_id = int(callback.data.rsplit("_", 1)[1])
    if callback.from_user.id != chat_id:
        await callback.answer("Это не ваши настройки", show_alert=True)
        return
    prefix, value = callback.data.rsplit("_", 1)[0].split("_", 1)
    sc, ov, freq = await get_settings(chat_id)
    if prefix == "tog":
        if value == "sc":
            sc = 1 - sc
        else:
            ov = 1 - ov
    elif prefix == "freq":
        freq = value
    await set_settings(chat_id, status_change=sc, overdue=ov, check_frequency=freq)
    await callback.message.edit_text(
        settings_text(sc, ov, freq),
        reply_markup=settings_kb(chat_id, sc, ov, freq),
        parse_mode="HTML",
    )
    await callback.answer()


async def main():
    logging.basicConfig(level=logging.INFO)
    await bot.delete_webhook()
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
