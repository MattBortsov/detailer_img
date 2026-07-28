"""Dedicated aiogram long-polling process."""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher

from car_wrap.bot.router import create_router
from car_wrap.config import AppSettings
from car_wrap.db.session import create_session_factory


async def run_polling(settings: AppSettings) -> None:
    """Own one bot/dispatcher lifecycle independently from FastAPI."""

    engine, sessions = create_session_factory(settings.database_url)
    bot = Bot(token=settings.bot_token.get_secret_value())
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_router(settings=settings, session_factory=sessions)
    )
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(run_polling(AppSettings.from_environment()))


if __name__ == "__main__":
    main()
