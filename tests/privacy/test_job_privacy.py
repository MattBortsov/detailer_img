"""Privacy canaries for durable acceptance, public API, logs, and Redis hints."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from car_wrap.api.app import create_app
from car_wrap.api.dependencies import (
    CurrentMiniAppSession,
    require_mini_app_session,
)
from car_wrap.config import AppSettings
from car_wrap.custom_colors.repository import CustomColorRepository
from car_wrap.db.models import ActiveSource, GenerationJob, JobOutbox
from car_wrap.jobs.relay import JobOutboxRelay
from car_wrap.jobs.repository import JobRepository
from car_wrap.jobs.service import JobAcceptanceService

pytestmark = [pytest.mark.postgresql, pytest.mark.asyncio]
NOW = datetime(2026, 7, 29, 8, 30, tzinfo=UTC)
CANARIES = (
    "IMAGE_BYTES_CANARY",
    "data:image/png;base64,BASE64_CANARY",
    "https://download.example/SIGNED_URL_CANARY",
    "BOT_TOKEN_CANARY",
    "OPENROUTER_KEY_CANARY",
    "RAW_INIT_DATA_CANARY",
    "Bearer AUTHORIZATION_CANARY",
    '{"provider_body":"PROVIDER_BODY_CANARY"}',
)


class RecordingPublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.messages.append((channel, message))
        return 1


async def test_private_canaries_never_cross_acceptance_boundaries(
    database_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(
            ActiveSource(
                telegram_user_id=1001,
                chat_id=1001,
                source_message_id=17,
                telegram_file_id="opaque-telegram-source",
                telegram_file_unique_id="opaque-telegram-unique",
                media_kind="photo",
                mime_type="image/jpeg",
                byte_size=1024,
                width=1200,
                height=800,
                accepted_at=NOW,
                updated_at=NOW,
            )
        )
        await session.commit()
    application = create_app(
        settings=AppSettings.model_validate(
            {
                "database_url": "postgresql+psycopg://user:pass@db/test",
                "bot_token": "safe-test-token",
                "bot_username": "CarWrapBot",
                "mini_app_url": "https://wrap.example.com/app",
            }
        ),
        session_factory=sessions,
        job_acceptance_service=JobAcceptanceService(
            repository=JobRepository(),
            custom_colors=CustomColorRepository(quota=20),
            image_model="x-ai/grok-imagine-image-quality",
            prompt_revision="vehicle-wrap-v1",
            max_active=1,
            max_recent=10,
            window_seconds=3600,
            clock=lambda: NOW,
        ),
    )
    application.dependency_overrides[require_mini_app_session] = lambda: (
        CurrentMiniAppSession(
            telegram_user_id=1001,
            expires_at=NOW + timedelta(minutes=15),
        )
    )
    submission = str(uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="https://testserver",
    ) as client:
        rejected = await client.post(
            "/api/v1/jobs",
            headers={"Authorization": CANARIES[6]},
            json={
                "color_id": "charcoal",
                "client_submission_uuid": submission,
                "untrusted_payload": "|".join(CANARIES),
            },
        )
        response = await client.post(
            "/api/v1/jobs",
            headers={"Authorization": CANARIES[6]},
            json={
                "color_id": "charcoal",
                "client_submission_uuid": submission,
            },
        )
    assert rejected.status_code == 422
    assert response.status_code == 202

    publisher = RecordingPublisher()
    relay = JobOutboxRelay(
        session_factory=sessions,
        publisher=publisher,
        channel="car-wrap.jobs",
        batch_size=10,
        clock=lambda: NOW,
    )
    assert await relay.run_once() == 1
    async with sessions() as session:
        job = await session.scalar(select(GenerationJob))
        outbox = await session.scalar(select(JobOutbox))
    assert job is not None and outbox is not None
    persisted = repr(
        {
            column.name: getattr(job, column.name)
            for column in GenerationJob.__table__.columns
        }
    ) + repr(
        {
            column.name: getattr(outbox, column.name)
            for column in JobOutbox.__table__.columns
        }
    )
    exposed = (
        rejected.text
        + response.text
        + repr(publisher.messages)
        + persisted
        + caplog.text
    )
    for canary in CANARIES:
        assert canary not in exposed
    assert publisher.messages == [("car-wrap.jobs", response.json()["job_id"])]
    assert job.error_code is None
    assert job.error_summary is None


async def test_acceptance_service_has_no_generation_or_delivery_boundary() -> None:
    source = inspect.getsource(JobAcceptanceService).lower()

    for forbidden in ("httpx", "openrouter", "aiogram", "send_photo", "download"):
        assert forbidden not in source
