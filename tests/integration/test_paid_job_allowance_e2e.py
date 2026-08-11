"""PostgreSQL allowance reservation lifecycle coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from car_wrap.billing.contracts import AllowanceKind, LedgerEntryKind
from car_wrap.custom_colors.repository import CustomColorRepository
from car_wrap.db.models import (
    ActiveSource,
    AllowanceBalance,
    AllowanceReservation,
    BillingLedgerEntry,
    GenerationJob,
)
from car_wrap.jobs.contracts import DeliveryReceipt, ExecutionErrorCode, ProviderReceipt
from car_wrap.jobs.repository import JobRepository
from car_wrap.jobs.service import JobAcceptanceService
from car_wrap.jobs.worker_repository import WorkerRepository

pytestmark = [pytest.mark.postgresql, pytest.mark.asyncio]
NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


def _acceptance() -> JobAcceptanceService:
    return JobAcceptanceService(
        repository=JobRepository(),
        custom_colors=CustomColorRepository(quota=20),
        image_model="x-ai/grok-imagine-image-quality",
        prompt_revision="vehicle-wrap-v1",
        max_active=1,
        max_recent=10,
        window_seconds=3600,
        clock=lambda: NOW,
    )


def _receipt() -> ProviderReceipt:
    return ProviderReceipt(
        provider_name="openrouter",
        request_id="request-1",
        status_code=200,
        latency_ms=10,
        input_tokens=1,
        output_tokens=1,
        total_tokens=2,
        cost_usd=Decimal("0.01"),
        output_byte_count=100,
        output_width=10,
        output_height=10,
        output_format="png",
        output_sha256="a" * 64,
    )


async def _seed_source(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session:
        session.add(
            ActiveSource(
                telegram_user_id=1001,
                chat_id=1001,
                source_message_id=1,
                telegram_file_id="source",
                telegram_file_unique_id="source-unique",
                media_kind="photo",
                mime_type="image/jpeg",
                byte_size=100,
                width=10,
                height=10,
                accepted_at=NOW,
                updated_at=NOW,
            )
        )
        await session.commit()


async def _claim_and_fail(
    sessions: async_sessionmaker[AsyncSession], job_id: object
) -> None:
    repository = WorkerRepository(CustomColorRepository(quota=20))
    async with sessions() as session:
        attempt = await repository.claim(
            session, worker_id="worker", now=NOW, lease_seconds=60, job_id=job_id
        )
        await session.commit()
    assert attempt is not None
    async with sessions() as session:
        await repository.mark_failed(
            session,
            attempt,
            ExecutionErrorCode.PROVIDER_AMBIGUOUS,
            summary="provider outcome cannot be confirmed",
            ambiguous=True,
            now=NOW,
        )
        await session.commit()


async def _claim_and_deliver(
    sessions: async_sessionmaker[AsyncSession], job_id: object
) -> None:
    repository = WorkerRepository(CustomColorRepository(quota=20))
    async with sessions() as session:
        attempt = await repository.claim(
            session, worker_id="worker", now=NOW, lease_seconds=60, job_id=job_id
        )
        assert attempt is not None
        await repository.mark_source_ready(session, attempt, now=NOW)
        await repository.mark_provider_started(session, attempt, now=NOW)
        await repository.mark_provider_succeeded(session, attempt, _receipt(), now=NOW)
        await repository.mark_delivering(session, attempt, now=NOW)
        await repository.mark_succeeded(
            session, attempt, DeliveryReceipt(chat_id=1001, message_id=33), now=NOW
        )
        await session.commit()


async def test_free_reservation_releases_on_ambiguous_failure_then_consumes_receipt(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    await _seed_source(sessions)
    service = _acceptance()
    async with sessions() as session:
        first = await service.accept(
            session, user_id=1001, color_id="charcoal", submission_uuid=uuid4()
        )
    await _claim_and_fail(sessions, first.job_id)
    async with sessions() as session:
        replay = await service.accept(
            session, user_id=1001, color_id="charcoal", submission_uuid=uuid4()
        )
    await _claim_and_deliver(sessions, replay.job_id)

    async with sessions() as session:
        balance = await session.scalar(
            select(AllowanceBalance).where(
                AllowanceBalance.telegram_user_id == 1001,
                AllowanceBalance.allowance_kind == AllowanceKind.FREE.value,
            )
        )
        reservations = (await session.scalars(select(AllowanceReservation))).all()
        entries = (await session.scalars(select(BillingLedgerEntry))).all()
        jobs = (await session.scalars(select(GenerationJob))).all()
    assert balance is not None
    assert (balance.granted_count, balance.reserved_count, balance.consumed_count) == (
        1,
        0,
        1,
    )
    assert sorted(reservation.status for reservation in reservations) == [
        "consumed",
        "released",
    ]
    assert {entry.entry_kind for entry in entries} >= {
        LedgerEntryKind.GRANT.value,
        LedgerEntryKind.RESERVE.value,
        LedgerEntryKind.RELEASE.value,
        LedgerEntryKind.CONSUME.value,
    }
    assert sorted(job.status for job in jobs) == ["failed", "succeeded"]
    assert all(job.lease_owner is None for job in jobs)
    assert all(job.lease_expires_at is None for job in jobs)
    assert all(job.terminal_at is not None for job in jobs)
