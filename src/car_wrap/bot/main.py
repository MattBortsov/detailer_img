"""Dedicated aiogram long-polling process."""

from __future__ import annotations

import asyncio

import httpx
from aiogram import Bot, Dispatcher

from car_wrap.billing.payments import PaymentService
from car_wrap.billing.runtime import start_subscription_scanner
from car_wrap.billing.subscriptions import SubscriptionService
from car_wrap.billing.tbank import TBankClient
from car_wrap.bot.router import create_router
from car_wrap.config import AppSettings
from car_wrap.custom_colors.runtime import build_custom_color_service
from car_wrap.db.session import create_session_factory
from car_wrap.jobs.repository import JobRepository
from car_wrap.jobs.service import JobAcceptanceService


async def run_polling(settings: AppSettings) -> None:
    """Own one bot/dispatcher lifecycle independently from FastAPI."""

    engine, sessions = create_session_factory(settings.database_url)
    bot = Bot(token=settings.bot_token.get_secret_value())
    provider_client = httpx.AsyncClient()
    custom_colors, _, custom_color_repository = build_custom_color_service(
        settings,
        provider_client=provider_client,
    )
    job_service = JobAcceptanceService(
        repository=JobRepository(),
        custom_colors=custom_color_repository,
        image_model=settings.openrouter_image_model,
        prompt_revision=settings.generation_prompt_revision,
        max_active=settings.job_max_active_per_user,
        max_recent=settings.job_max_accepted_per_window,
        window_seconds=settings.job_limit_window_seconds,
    )
    dispatcher = Dispatcher()
    payments = PaymentService(sessions, TBankClient(settings))
    subscriptions = SubscriptionService(sessions, payments)
    dispatcher.include_router(
        create_router(
            settings=settings,
            session_factory=sessions,
            custom_color_service=custom_colors,
            job_acceptance_service=job_service,
            payment_service=payments,
        )
    )
    subscription_stop, subscription_task = start_subscription_scanner(
        subscriptions,
        bot,
        interval_seconds=settings.subscription_scan_seconds,
    )
    try:
        await dispatcher.start_polling(bot)
    finally:
        subscription_stop.set()
        subscription_task.cancel()
        try:
            await subscription_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        await provider_client.aclose()
        await engine.dispose()


def main() -> None:
    asyncio.run(run_polling(AppSettings.from_environment()))


if __name__ == "__main__":
    main()
