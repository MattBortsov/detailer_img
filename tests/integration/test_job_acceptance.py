"""Atomic PostgreSQL job-acceptance behavior."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

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
NOW = datetime(2026, 7, 29, 6, 30, tzinfo=UTC)


def service(*, max_active: int = 1, max_recent: int = 10) -> JobAcceptanceService:
    return JobAcceptanceService(
        repository=JobRepository(),
        custom_colors=CustomColorRepository(quota=20),
        image_model="x-ai/grok-imagine-image-quality",
        prompt_revision="vehicle-wrap-v1",
        max_active=max_active,
        max_recent=max_recent,
        window_seconds=3600,
        clock=lambda: NOW,
    )


async def seed_source(
    sessions: async_sessionmaker[AsyncSession],
    *,
    user_id: int = 1001,
    message_id: int = 17,
    file_id: str = "source-original",
) -> None:
    async with sessions() as session:
        session.add(
            ActiveSource(
                telegram_user_id=user_id,
                chat_id=user_id,
                source_message_id=message_id,
                telegram_file_id=file_id,
                telegram_file_unique_id=f"unique-{message_id}",
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


@pytest.mark.parametrize("color_id", ["charcoal", "surprise_me"])
async def test_accepts_server_owned_intents(
    database_engine: AsyncEngine,
    color_id: str,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    await seed_source(sessions)
    async with sessions() as session:
        accepted = await service().accept(
            session,
            user_id=1001,
            color_id=color_id,
            submission_uuid=uuid4(),
        )
    async with sessions() as session:
        job = await session.get(GenerationJob, accepted.job_id)
        outbox = await session.get(JobOutbox, accepted.job_id)
    assert job is not None and outbox is not None
    assert job.intent_kind == ("surprise" if color_id == "surprise_me" else "palette")
    assert job.telegram_file_id == "source-original"


async def test_concurrent_replay_creates_one_job_and_one_outbox(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    await seed_source(sessions)
    submission = uuid4()

    async def accept_once() -> object:
        async with sessions() as session:
            return await service().accept(
                session,
                user_id=1001,
                color_id="charcoal",
                submission_uuid=submission,
            )

    first, second = await asyncio.gather(accept_once(), accept_once())
    assert first == second
    async with sessions() as session:
        jobs = await session.scalar(select(func.count(GenerationJob.id)))
        outbox = await session.scalar(select(func.count(JobOutbox.job_id)))
    assert jobs == 1
    assert outbox == 1


async def test_concurrent_new_submissions_obey_active_limit(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    await seed_source(sessions)

    async def accept_once() -> str:
        async with sessions() as session:
            try:
                await service().accept(
                    session,
                    user_id=1001,
                    color_id="charcoal",
                    submission_uuid=uuid4(),
                )
                return "accepted"
            except JobAcceptanceError as error:
                return error.code.value

    outcomes = await asyncio.gather(accept_once(), accept_once())
    assert sorted(outcomes) == ["accepted", AcceptanceErrorCode.ACTIVE_LIMIT.value]


async def test_job_keeps_source_snapshot_after_new_photo(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    await seed_source(sessions)
    async with sessions() as session:
        accepted = await service().accept(
            session,
            user_id=1001,
            color_id="charcoal",
            submission_uuid=uuid4(),
        )
    async with sessions() as session:
        await session.execute(
            update(ActiveSource)
            .where(ActiveSource.telegram_user_id == 1001)
            .values(source_message_id=18, telegram_file_id="source-new")
        )
        await session.commit()
    async with sessions() as session:
        job = await session.get(GenerationJob, accepted.job_id)
    assert job is not None
    assert (job.source_message_id, job.telegram_file_id) == (17, "source-original")


async def test_custom_version_is_retained_once_for_replay(
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
    submission = uuid4()
    selected = custom_selection_id(color.id, 1)
    for _ in range(2):
        async with sessions() as session:
            await service().accept(
                session,
                user_id=1001,
                color_id=selected,
                submission_uuid=submission,
            )
    async with sessions() as session:
        version = await session.scalar(
            select(CustomColorVersion).where(
                CustomColorVersion.custom_color_id == color.id
            )
        )
    assert version is not None
    assert version.retain_count == 1
