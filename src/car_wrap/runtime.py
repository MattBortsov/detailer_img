"""Production dependency construction for the FastAPI process."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from aiogram import Bot
from fastapi import FastAPI

from car_wrap.api.app import create_app
from car_wrap.config import AppSettings
from car_wrap.custom_colors.media import (
    ClamdInstreamScanner,
    MediaPolicy,
    normalize_reference,
)
from car_wrap.custom_colors.moderation import (
    ModerationResult,
    moderate_reference,
)
from car_wrap.custom_colors.repository import CustomColorRepository
from car_wrap.custom_colors.service import CustomColorService
from car_wrap.custom_colors.storage import FilesystemPrivateStorage
from car_wrap.db.session import create_session_factory
from car_wrap.jobs.repository import JobRepository
from car_wrap.jobs.service import JobAcceptanceService


def build_application() -> FastAPI:
    """Build production resources once per Uvicorn worker."""

    settings = AppSettings.from_environment()
    engine, sessions = create_session_factory(settings.database_url)
    storage = FilesystemPrivateStorage(
        settings.custom_color_storage_root,
        max_object_bytes=settings.custom_color_max_bytes,
    )
    repository = CustomColorRepository(quota=settings.custom_color_quota)
    job_service = JobAcceptanceService(
        repository=JobRepository(),
        custom_colors=repository,
        image_model=settings.openrouter_image_model,
        prompt_revision=settings.generation_prompt_revision,
        max_active=settings.job_max_active_per_user,
        max_recent=settings.job_max_accepted_per_window,
        window_seconds=settings.job_limit_window_seconds,
    )
    scanner = ClamdInstreamScanner(
        settings.clamav_socket_path,
        max_bytes=settings.custom_color_max_bytes,
    )
    media_policy = MediaPolicy(
        max_bytes=settings.custom_color_max_bytes,
        max_side_px=settings.custom_color_max_side_px,
        max_pixels=settings.custom_color_max_pixels,
        max_frames=settings.custom_color_max_frames,
        output_long_edge_px=settings.custom_color_output_long_edge_px,
        decode_timeout_seconds=settings.custom_color_decode_timeout_seconds,
    )
    provider_client = httpx.AsyncClient()
    telegram_bot = Bot(token=settings.bot_token.get_secret_value())
    api_key = os.environ.get("OPENROUTER_API_KEY")

    async def moderate(data: bytes) -> ModerationResult:
        return await moderate_reference(
            data,
            client=provider_client,
            api_key=api_key,
            model=settings.moderation_vision_model,
        )

    service = CustomColorService(
        storage=storage,
        repository=repository,
        normalize=lambda data, mime: normalize_reference(
            data,
            declared_mime=mime,
            scanner=scanner,
            policy=media_policy,
        ),
        moderate=moderate,
        moderation_model=settings.moderation_vision_model,
    )

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
        lifespan=lifespan,
    )
