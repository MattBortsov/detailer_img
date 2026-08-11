"""Production dependency construction for the FastAPI process."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from aiogram import Bot
from fastapi import FastAPI

from car_wrap.api.app import create_app
from car_wrap.billing.gateway import PaymentGatewayClient
from car_wrap.billing.payments import PaymentService
from car_wrap.config import AppSettings
from car_wrap.custom_colors.runtime import build_custom_color_service
from car_wrap.db.session import create_session_factory
from car_wrap.jobs.repository import JobRepository
from car_wrap.jobs.service import JobAcceptanceService


def build_application() -> FastAPI:
    """Build production resources once per Uvicorn worker."""

    settings = AppSettings.from_environment()
    engine, sessions = create_session_factory(settings.database_url)
    provider_client = httpx.AsyncClient()
    service, storage, repository = build_custom_color_service(
        settings,
        provider_client=provider_client,
    )
    job_service = JobAcceptanceService(
        repository=JobRepository(),
        custom_colors=repository,
        image_model=settings.openrouter_image_model,
        prompt_revision=settings.generation_prompt_revision,
        max_active=settings.job_max_active_per_user,
        max_recent=settings.job_max_accepted_per_window,
        window_seconds=settings.job_limit_window_seconds,
    )
    telegram_bot = Bot(token=settings.bot_token.get_secret_value())
    payment_service = PaymentService(sessions, PaymentGatewayClient(settings))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        try:
            yield
        finally:
            await telegram_bot.session.close()
            await provider_client.aclose()
            await engine.dispose()

    return create_app(
        settings=settings,
        session_factory=sessions,
        custom_color_service=service,
        custom_color_storage=storage,
        custom_color_repository=repository,
        job_acceptance_service=job_service,
        telegram_bot=telegram_bot,
        payment_service=payment_service,
        lifespan=lifespan,
    )
