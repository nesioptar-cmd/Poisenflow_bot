import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from bot import bot, dp
from database import init_db
from server import router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    polling_task = asyncio.create_task(dp.start_polling(bot))
    yield
    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass
    await bot.session.close()


app = FastAPI(lifespan=lifespan)
app.include_router(router)
