"""Send one UTC daily activity report to configured administrators."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot

from car_wrap.config import AppSettings
from car_wrap.db.session import create_session_factory
from car_wrap.services.telegram_users import (
    daily_stats,
    mark_report_sent,
    report_was_sent,
)

logger = logging.getLogger(__name__)


async def send_due_report(settings: AppSettings, bot: Bot) -> None:
    now = datetime.now(UTC)
    if now.hour < settings.daily_stats_hour_utc:
        return
    report_date = (now - timedelta(days=1)).date()
    engine, sessions = create_session_factory(settings.database_url)
    try:
        async with sessions() as session:
            stats = await daily_stats(session, report_date)
        for admin_id in settings.admin_telegram_user_ids:
            async with sessions() as session:
                if await report_was_sent(session, report_date, admin_id):
                    continue
            try:
                await bot.send_message(admin_id, stats.text())
            except Exception:
                logger.exception(
                    "daily statistics delivery failed", extra={"admin_id": admin_id}
                )
                continue
            async with sessions() as session:
                await mark_report_sent(session, report_date, admin_id)
                await session.commit()
    finally:
        await engine.dispose()


async def run() -> None:
    settings = AppSettings.from_environment()
    bot = Bot(token=settings.bot_token.get_secret_value())
    try:
        while True:
            await send_due_report(settings, bot)
            await asyncio.sleep(60)
    finally:
        await bot.session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
