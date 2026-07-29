"""Real PostgreSQL claim, receipt, crash, and release semantics."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from car_wrap.custom_colors.repository import CustomColorRepository
from car_wrap.db.models import (
    CustomColor,
    CustomColorVersion,
    GenerationAttempt,
    GenerationJob,
)
from car_wrap.jobs.contracts import (
    DeliveryReceipt,
    ExecutionErrorCode,
    ProviderReceipt,
)
from car_wrap.jobs.worker_repository import LostLeaseError, WorkerRepository

pytestmark = [pytest.mark.postgresql, pytest.mark.asyncio]
NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)


def _job(
    *,
    job_id: UUID | None = None,
    custom_version_id: UUID | None = None,
) -> GenerationJob:
    custom = custom_version_id is not None
    return GenerationJob(
        id=job_id or uuid4(),
        telegram_user_id=1001,
        client_submission_uuid=uuid4(),
        chat_id=1001,
        source_message_id=17,
        telegram_file_id="source-file-id",
        telegram_file_unique_id="source-unique-id",
        source_media_kind="photo",
        source_mime_type="image/jpeg",
        source_byte_size=1024,
        source_width=1200,
        source_height=800,
        intent_kind="custom" if custom else "palette",
        palette_color_id=None if custom else "charcoal",
        custom_color_version_id=custom_version_id,
        custom_color_sha256="a" * 64 if custom else None,
        intent_display_name="Bronze" if custom else "Графитовый",
        image_model="x-ai/grok-imagine-image-quality",
        prompt_revision="vehicle-wrap-v1",
        status="queued",
        created_at=NOW,
        updated_at=NOW,
    )


def _provider_receipt() -> ProviderReceipt:
    return ProviderReceipt(
        provider_name="openrouter",
        request_id="req-1",
        status_code=200,
        latency_ms=100,
        input_tokens=1,
        output_tokens=2,
        total_tokens=3,
        cost_usd=Decimal("0.01"),
        output_byte_count=100,
        output_width=100,
        output_height=80,
        output_format="png",
        output_sha256="b" * 64,
    )


async def _seed_custom(
    sessions: async_sessionmaker[AsyncSession],
) -> UUID:
    color_id = uuid4()
    version_id = uuid4()
    async with sessions() as session:
        session.add(
            CustomColor(
                id=color_id,
                telegram_user_id=2002,
                display_name="Bronze",
                status="approved",
                current_version=1,
                approved_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            CustomColorVersion(
                id=version_id,
                custom_color_id=color_id,
                version=1,
                object_key="aa/bb/" + "c" * 32 + ".png",
                sha256="a" * 64,
                byte_size=128,
                width=64,
                height=64,
                retain_count=1,
                created_at=NOW,
            )
        )
        await session.commit()
    return version_id


async def _claim(
    sessions: async_sessionmaker[AsyncSession],
    repository: WorkerRepository,
    worker_id: str,
    *,
    now: datetime = NOW,
    job_id: UUID | None = None,
) -> object:
    async with sessions() as session:
        attempt = await repository.claim(
            session,
            worker_id=worker_id,
            now=now,
            lease_seconds=60,
            job_id=job_id,
        )
        await session.commit()
        return attempt


async def test_competing_workers_claim_one_job_once(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(_job())
        await session.commit()
    repository = WorkerRepository(CustomColorRepository(quota=20))

    claims = await asyncio.gather(
        _claim(sessions, repository, "worker-a"),
        _claim(sessions, repository, "worker-b"),
    )

    assert sum(claim is not None for claim in claims) == 1
    async with sessions() as session:
        attempts = (await session.scalars(select(GenerationAttempt))).all()
    assert len(attempts) == 1


async def test_pre_provider_expiry_reclaims_but_post_provider_reconciles(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    first_job = _job()
    second_job = _job()
    async with sessions() as session:
        session.add_all([first_job, second_job])
        await session.commit()
    repository = WorkerRepository(CustomColorRepository(quota=20))

    first = await _claim(
        sessions,
        repository,
        "worker-a",
        job_id=first_job.id,
    )
    assert first is not None
    reclaimed = await _claim(
        sessions,
        repository,
        "worker-b",
        now=NOW + timedelta(seconds=61),
        job_id=first_job.id,
    )
    assert reclaimed is not None
    assert reclaimed.attempt_number == 2

    post_provider = await _claim(
        sessions,
        repository,
        "worker-c",
        job_id=second_job.id,
    )
    assert post_provider is not None
    async with sessions() as session:
        await repository.mark_source_ready(session, post_provider, now=NOW)
        await repository.mark_provider_started(session, post_provider, now=NOW)
        await session.commit()

    assert (
        await _claim(
            sessions,
            repository,
            "worker-d",
            now=NOW + timedelta(seconds=61),
            job_id=second_job.id,
        )
        is None
    )
    async with sessions() as session:
        count = await repository.reconcile_expired(
            session,
            now=NOW + timedelta(seconds=61),
        )
        await session.commit()
    assert count == 1
    async with sessions() as session:
        row = await session.get(GenerationJob, second_job.id)
    assert row is not None
    assert (row.status, row.error_code, row.result_message_id) == (
        "failed",
        ExecutionErrorCode.PROVIDER_AMBIGUOUS.value,
        None,
    )


async def test_success_requires_receipt_and_releases_custom_once(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await _seed_custom(sessions)
    job = _job(custom_version_id=version_id)
    async with sessions() as session:
        session.add(job)
        await session.commit()
    repository = WorkerRepository(CustomColorRepository(quota=20))
    attempt = await _claim(sessions, repository, "worker-a")
    assert attempt is not None

    async with sessions() as session:
        await repository.mark_source_ready(session, attempt, now=NOW)
        await repository.mark_provider_started(session, attempt, now=NOW)
        await repository.mark_provider_succeeded(
            session,
            attempt,
            _provider_receipt(),
            now=NOW,
        )
        await repository.mark_delivering(session, attempt, now=NOW)
        await session.commit()

    async with sessions() as session:
        with pytest.raises(ValueError, match="invalid Telegram delivery receipt"):
            await repository.mark_succeeded(
                session,
                attempt,
                DeliveryReceipt(chat_id=999, message_id=30),
                now=NOW,
            )
        await session.rollback()

    async with sessions() as session:
        await repository.mark_succeeded(
            session,
            attempt,
            DeliveryReceipt(chat_id=1001, message_id=30),
            now=NOW,
        )
        await session.commit()
    async with sessions() as session:
        stored_job = await session.get(GenerationJob, job.id)
        version = await session.get(CustomColorVersion, version_id)
    assert stored_job is not None and version is not None
    assert (stored_job.status, stored_job.result_message_id) == ("succeeded", 30)
    assert (
        version.retain_count,
        stored_job.custom_reference_released_at is not None,
    ) == (
        0,
        True,
    )
    async with sessions() as session:
        with pytest.raises(LostLeaseError):
            await repository.mark_failed(
                session,
                attempt,
                ExecutionErrorCode.INTERNAL_FAILURE,
                summary="late duplicate terminalization",
                ambiguous=False,
                now=NOW,
            )
    async with sessions() as session:
        version = await session.get(CustomColorVersion, version_id)
    assert version is not None and version.retain_count == 0
