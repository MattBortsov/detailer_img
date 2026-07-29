"""Black-box durability checks not duplicated by lower-level job tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from car_wrap.api.app import create_app
from car_wrap.api.dependencies import (
    CurrentMiniAppSession,
    require_mini_app_session,
)
from car_wrap.config import AppSettings
from car_wrap.custom_colors.moderation import normalize_display_name
from car_wrap.custom_colors.repository import (
    ColorStatus,
    CustomColorRepository,
    VersionInput,
)
from car_wrap.db.models import (
    ActiveSource,
    CustomColorVersion,
    GenerationJob,
    JobOutbox,
)
from car_wrap.jobs.contracts import AcceptanceErrorCode, JobAcceptanceError
from car_wrap.jobs.repository import JobRepository
from car_wrap.jobs.service import JobAcceptanceService
from car_wrap.palette import custom_selection_id

pytestmark = [pytest.mark.postgresql, pytest.mark.asyncio]
NOW = datetime(2026, 7, 29, 8, tzinfo=UTC)


def build_service(
    *,
    repository: JobRepository | None = None,
    max_active: int = 1,
    max_recent: int = 10,
) -> JobAcceptanceService:
    return JobAcceptanceService(
        repository=repository or JobRepository(),
        custom_colors=CustomColorRepository(quota=20),
        image_model="x-ai/grok-imagine-image-quality",
        prompt_revision="vehicle-wrap-v1",
        max_active=max_active,
        max_recent=max_recent,
        window_seconds=3600,
        clock=lambda: NOW,
    )


async def seed_source(sessions: async_sessionmaker[AsyncSession]) -> None:
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


def settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "database_url": "postgresql+psycopg://user:pass@db/test",
            "bot_token": "test-token",
            "bot_username": "CarWrapBot",
            "mini_app_url": "https://wrap.example.com/app",
        }
    )


async def test_api_replay_after_app_rebuild_returns_one_durable_job(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    await seed_source(sessions)
    body = {
        "color_id": "charcoal",
        "client_submission_uuid": str(uuid4()),
    }

    async def submit_with_new_app() -> tuple[int, dict[str, object]]:
        application = create_app(
            settings=settings(),
            session_factory=sessions,
            job_acceptance_service=build_service(),
        )
        application.dependency_overrides[require_mini_app_session] = lambda: (
            CurrentMiniAppSession(
                telegram_user_id=1001,
                expires_at=NOW + timedelta(minutes=15),
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="https://testserver",
        ) as client:
            response = await client.post("/api/v1/jobs", json=body)
        return response.status_code, response.json()

    first = await submit_with_new_app()
    replay_after_restart = await submit_with_new_app()

    assert first == replay_after_restart
    assert first[0] == 202
    async with sessions() as session:
        jobs = await session.scalar(select(func.count(GenerationJob.id)))
        outbox = await session.scalar(select(func.count(JobOutbox.job_id)))
    assert (jobs, outbox) == (1, 1)


async def test_recent_limit_is_atomic_after_prior_job_finishes(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    await seed_source(sessions)
    service = build_service(max_active=1, max_recent=1)
    async with sessions() as session:
        accepted = await service.accept(
            session,
            user_id=1001,
            color_id="charcoal",
            submission_uuid=uuid4(),
        )
    async with sessions() as session:
        await session.execute(
            update(GenerationJob)
            .where(GenerationJob.id == accepted.job_id)
            .values(
                status="succeeded",
                result_message_id=999,
                terminal_at=NOW,
                updated_at=NOW,
            )
        )
        await session.commit()

    async with sessions() as session:
        with pytest.raises(JobAcceptanceError) as caught:
            await service.accept(
                session,
                user_id=1001,
                color_id="copper",
                submission_uuid=uuid4(),
            )
    assert caught.value.code is AcceptanceErrorCode.RECENT_LIMIT


class FailingRepository(JobRepository):
    async def create(self, *args: object, **kwargs: object) -> GenerationJob:
        await super().create(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("pre-commit failure")


async def test_precommit_failure_rolls_back_job_outbox_and_custom_retain(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    await seed_source(sessions)
    colors = CustomColorRepository(quota=20)
    async with sessions() as session:
        color = await colors.create(
            session,
            owner_id=2002,
            display_name=normalize_display_name("Bronze"),
            version=VersionInput(
                object_key="aa/bb/" + "c" * 32 + ".png",
                sha256="a" * 64,
                byte_size=128,
                width=64,
                height=64,
            ),
        )
        await colors.transition(
            session,
            color_id=color.id,
            target=ColorStatus.APPROVED,
            reason_code="approved",
        )
        await session.commit()
    selected = custom_selection_id(color.id, 1)
    service = JobAcceptanceService(
        repository=FailingRepository(),
        custom_colors=colors,
        image_model="x-ai/grok-imagine-image-quality",
        prompt_revision="vehicle-wrap-v1",
        max_active=1,
        max_recent=10,
        window_seconds=3600,
        clock=lambda: NOW,
    )

    async with sessions() as session:
        with pytest.raises(RuntimeError, match="pre-commit failure"):
            await service.accept(
                session,
                user_id=1001,
                color_id=selected,
                submission_uuid=uuid4(),
            )

    async with sessions() as session:
        jobs = await session.scalar(select(func.count(GenerationJob.id)))
        outbox = await session.scalar(select(func.count(JobOutbox.job_id)))
        version = await session.scalar(
            select(CustomColorVersion).where(
                CustomColorVersion.custom_color_id == color.id
            )
        )
    assert version is not None
    assert (jobs, outbox, version.retain_count) == (0, 0, 0)
