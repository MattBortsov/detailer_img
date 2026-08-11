"""PostgreSQL authority for worker claims and external side-effect markers."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.billing.allowances import AllowanceService
from car_wrap.custom_colors.repository import CustomColorRepository
from car_wrap.db.models import CustomColorVersion, GenerationAttempt, GenerationJob
from car_wrap.jobs.contracts import (
    AttemptState,
    ClaimedAttempt,
    DeliveryReceipt,
    ExecutionErrorCode,
    IntentKind,
    ProviderReceipt,
)


class LostLeaseError(RuntimeError):
    """The caller no longer owns the job attempt."""


class WorkerRepository:
    def __init__(
        self,
        custom_colors: CustomColorRepository,
        allowances: AllowanceService | None = None,
    ) -> None:
        self._custom_colors = custom_colors
        self._allowances = allowances or AllowanceService()

    async def claim(
        self,
        session: AsyncSession,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        job_id: UUID | None = None,
    ) -> ClaimedAttempt | None:
        if not worker_id or lease_seconds <= 0:
            raise ValueError("invalid worker claim")
        previous_provider_start = exists().where(
            GenerationAttempt.job_id == GenerationJob.id,
            GenerationAttempt.provider_started_at.is_not(None),
        )
        statement = (
            select(GenerationJob)
            .where(
                (GenerationJob.status == "queued")
                | (
                    (GenerationJob.status == "running")
                    & (GenerationJob.lease_expires_at < now)
                    & ~previous_provider_start
                )
            )
            .order_by(GenerationJob.created_at, GenerationJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job_id is not None:
            statement = statement.where(GenerationJob.id == job_id)
        job = cast(GenerationJob | None, await session.scalar(statement))
        if job is None:
            return None

        if job.status == "running":
            previous = await session.scalar(
                select(GenerationAttempt)
                .where(
                    GenerationAttempt.job_id == job.id,
                    GenerationAttempt.attempt_number == job.attempt_count,
                )
                .with_for_update()
            )
            if previous is not None:
                previous.state = AttemptState.FAILED.value
                previous.error_code = ExecutionErrorCode.INTERNAL_FAILURE.value
                previous.error_summary = "worker lease expired before provider start"
                previous.completed_at = now
                previous.updated_at = now

        job.attempt_count += 1
        job.status = "running"
        job.lease_owner = worker_id
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.updated_at = now
        attempt = GenerationAttempt(
            id=uuid4(),
            job_id=job.id,
            attempt_number=job.attempt_count,
            worker_id=worker_id,
            state=AttemptState.CLAIMED.value,
            started_at=now,
            updated_at=now,
        )
        session.add(attempt)
        await session.flush()
        return self._snapshot(job, attempt)

    async def heartbeat(
        self,
        session: AsyncSession,
        attempt: ClaimedAttempt,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> None:
        job, _ = await self._owned(session, attempt, now=now)
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.updated_at = now
        await session.flush()

    async def resolve_custom_version(
        self,
        session: AsyncSession,
        attempt: ClaimedAttempt,
    ) -> CustomColorVersion | None:
        """Resolve only the retained immutable version snapshotted by the job."""

        if (
            attempt.intent_kind is not IntentKind.CUSTOM
            or attempt.custom_color_version_id is None
            or attempt.custom_color_sha256 is None
        ):
            return None
        return cast(
            CustomColorVersion | None,
            await session.scalar(
                select(CustomColorVersion).where(
                    CustomColorVersion.id == attempt.custom_color_version_id,
                    CustomColorVersion.sha256 == attempt.custom_color_sha256,
                    CustomColorVersion.retain_count > 0,
                )
            ),
        )

    async def mark_source_ready(
        self,
        session: AsyncSession,
        attempt: ClaimedAttempt,
        *,
        now: datetime,
    ) -> None:
        _, row = await self._owned(session, attempt, now=now)
        self._transition(row, AttemptState.SOURCE_READY, now)
        await session.flush()

    async def mark_provider_started(
        self,
        session: AsyncSession,
        attempt: ClaimedAttempt,
        *,
        now: datetime,
    ) -> None:
        _, row = await self._owned(session, attempt, now=now)
        if row.state != AttemptState.SOURCE_READY.value:
            raise ValueError("provider start requires source-ready state")
        row.provider_started_at = now
        self._transition(row, AttemptState.PROVIDER_STARTED, now)
        await session.flush()

    async def record_safe_preupload_retry(
        self,
        session: AsyncSession,
        attempt: ClaimedAttempt,
        *,
        now: datetime,
    ) -> None:
        _, row = await self._owned(session, attempt, now=now)
        if (
            row.state != AttemptState.PROVIDER_STARTED.value
            or row.safe_preupload_retries != 0
        ):
            raise ValueError("safe retry budget exhausted")
        row.safe_preupload_retries = 1
        row.updated_at = now
        await session.flush()

    async def mark_provider_succeeded(
        self,
        session: AsyncSession,
        attempt: ClaimedAttempt,
        receipt: ProviderReceipt,
        *,
        now: datetime,
    ) -> None:
        _, row = await self._owned(session, attempt, now=now)
        if row.state != AttemptState.PROVIDER_STARTED.value:
            raise ValueError("provider receipt requires started state")
        row.provider_completed_at = now
        row.provider_name = receipt.provider_name
        row.provider_request_id = receipt.request_id
        row.provider_status_code = receipt.status_code
        row.provider_latency_ms = receipt.latency_ms
        row.input_tokens = receipt.input_tokens
        row.output_tokens = receipt.output_tokens
        row.total_tokens = receipt.total_tokens
        row.cost_usd = receipt.cost_usd
        row.output_byte_count = receipt.output_byte_count
        row.output_width = receipt.output_width
        row.output_height = receipt.output_height
        row.output_format = receipt.output_format
        row.output_sha256 = receipt.output_sha256
        self._transition(row, AttemptState.PROVIDER_SUCCEEDED, now)
        await session.flush()

    async def mark_delivering(
        self,
        session: AsyncSession,
        attempt: ClaimedAttempt,
        *,
        now: datetime,
    ) -> None:
        _, row = await self._owned(session, attempt, now=now)
        if row.state != AttemptState.PROVIDER_SUCCEEDED.value:
            raise ValueError("delivery requires provider success")
        row.delivery_started_at = now
        self._transition(row, AttemptState.DELIVERING, now)
        await session.flush()

    async def mark_succeeded(
        self,
        session: AsyncSession,
        attempt: ClaimedAttempt,
        receipt: DeliveryReceipt,
        *,
        now: datetime,
    ) -> None:
        job, row = await self._owned(session, attempt, now=now)
        if row.state != AttemptState.DELIVERING.value:
            raise ValueError("success requires delivery start")
        if receipt.chat_id != job.chat_id or receipt.message_id <= 0:
            raise ValueError("invalid Telegram delivery receipt")
        row.state = AttemptState.SUCCEEDED.value
        row.result_message_id = receipt.message_id
        row.completed_at = now
        row.updated_at = now
        job.status = "succeeded"
        job.result_message_id = receipt.message_id
        await self._finish_job(session, job, now=now)
        await self._allowances.consume_after_receipt(session, job_id=job.id, now=now)

    async def mark_failed(
        self,
        session: AsyncSession,
        attempt: ClaimedAttempt,
        code: ExecutionErrorCode,
        *,
        summary: str,
        ambiguous: bool,
        now: datetime,
    ) -> None:
        job, row = await self._owned(session, attempt, now=now, allow_expired=True)
        row.state = (
            AttemptState.AMBIGUOUS.value if ambiguous else AttemptState.FAILED.value
        )
        row.error_code = code.value
        row.error_summary = summary[:240]
        row.completed_at = now
        row.updated_at = now
        job.status = "failed"
        job.error_code = code.value
        job.error_summary = summary[:240]
        await self._finish_job(session, job, now=now)
        await self._allowances.release_terminal(session, job_id=job.id, now=now)

    async def reconcile_expired(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        limit: int = 10,
    ) -> int:
        """Terminalize expired attempts that may have crossed a side effect."""

        jobs = (
            await session.scalars(
                select(GenerationJob)
                .where(
                    GenerationJob.status == "running",
                    GenerationJob.lease_expires_at < now,
                    exists().where(
                        GenerationAttempt.job_id == GenerationJob.id,
                        GenerationAttempt.provider_started_at.is_not(None),
                    ),
                )
                .order_by(GenerationJob.lease_expires_at, GenerationJob.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        reconciled = 0
        for job in jobs:
            row = await session.scalar(
                select(GenerationAttempt)
                .where(
                    GenerationAttempt.job_id == job.id,
                    GenerationAttempt.attempt_number == job.attempt_count,
                )
                .with_for_update()
            )
            if row is None:
                continue
            code = (
                ExecutionErrorCode.DELIVERY_AMBIGUOUS
                if row.delivery_started_at is not None
                else ExecutionErrorCode.PROVIDER_AMBIGUOUS
            )
            snapshot = self._snapshot(job, row)
            await self.mark_failed(
                session,
                snapshot,
                code,
                summary=(
                    "external side effect could not be confirmed after lease expiry"
                ),
                ambiguous=True,
                now=now,
            )
            reconciled += 1
        return reconciled

    async def _finish_job(
        self,
        session: AsyncSession,
        job: GenerationJob,
        *,
        now: datetime,
    ) -> None:
        job.terminal_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.updated_at = now
        if (
            job.custom_color_version_id is not None
            and job.custom_reference_released_at is None
        ):
            await self._custom_colors.release(
                session,
                version_id=job.custom_color_version_id,
            )
            job.custom_reference_released_at = now
        await session.flush()

    async def _owned(
        self,
        session: AsyncSession,
        attempt: ClaimedAttempt,
        *,
        now: datetime,
        allow_expired: bool = False,
    ) -> tuple[GenerationJob, GenerationAttempt]:
        job = await session.scalar(
            select(GenerationJob)
            .where(GenerationJob.id == attempt.job_id)
            .with_for_update()
        )
        row = await session.scalar(
            select(GenerationAttempt)
            .where(GenerationAttempt.id == attempt.attempt_id)
            .with_for_update()
        )
        if (
            job is None
            or row is None
            or job.status != "running"
            or job.lease_owner != attempt.worker_id
            or row.worker_id != attempt.worker_id
            or row.attempt_number != job.attempt_count
            or (
                not allow_expired
                and (job.lease_expires_at is None or job.lease_expires_at < now)
            )
        ):
            raise LostLeaseError
        return job, row

    @staticmethod
    def _transition(
        attempt: GenerationAttempt,
        state: AttemptState,
        now: datetime,
    ) -> None:
        attempt.state = state.value
        attempt.updated_at = now

    @staticmethod
    def _snapshot(job: GenerationJob, attempt: GenerationAttempt) -> ClaimedAttempt:
        return ClaimedAttempt(
            job_id=job.id,
            attempt_id=attempt.id,
            attempt_number=attempt.attempt_number,
            worker_id=attempt.worker_id,
            lease_expires_at=cast(datetime, job.lease_expires_at),
            telegram_user_id=job.telegram_user_id,
            chat_id=job.chat_id,
            source_message_id=job.source_message_id,
            telegram_file_id=job.telegram_file_id,
            source_media_kind=job.source_media_kind,
            source_mime_type=job.source_mime_type,
            source_byte_size=job.source_byte_size,
            source_width=job.source_width,
            source_height=job.source_height,
            intent_kind=IntentKind(job.intent_kind),
            intent_display_name=job.intent_display_name,
            palette_color_id=job.palette_color_id,
            custom_color_version_id=job.custom_color_version_id,
            custom_color_sha256=job.custom_color_sha256,
            image_model=job.image_model,
            prompt_revision=job.prompt_revision,
        )
