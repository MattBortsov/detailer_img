"""PostgreSQL authority for idempotent job acceptance."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.db.models import ActiveSource, GenerationJob, JobOutbox
from car_wrap.jobs.contracts import (
    AcceptanceErrorCode,
    IntentSnapshot,
    JobAcceptanceError,
    SourceSnapshot,
)


class JobRepository:
    async def lock_user(self, session: AsyncSession, user_id: int) -> None:
        await session.execute(select(func.pg_advisory_xact_lock(user_id)))

    async def existing(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        submission_uuid: UUID,
    ) -> GenerationJob | None:
        return cast(
            GenerationJob | None,
            await session.scalar(
                select(GenerationJob).where(
                    GenerationJob.telegram_user_id == user_id,
                    GenerationJob.client_submission_uuid == submission_uuid,
                )
            ),
        )

    async def source(
        self,
        session: AsyncSession,
        *,
        user_id: int,
    ) -> ActiveSource | None:
        return cast(
            ActiveSource | None,
            await session.scalar(
                select(ActiveSource)
                .where(ActiveSource.telegram_user_id == user_id)
                .with_for_update()
            ),
        )

    async def enforce_limits(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        now: datetime,
        max_active: int,
        max_recent: int,
        window_seconds: int,
    ) -> None:
        active = await session.scalar(
            select(func.count(GenerationJob.id)).where(
                GenerationJob.telegram_user_id == user_id,
                GenerationJob.status.in_(("queued", "running")),
            )
        )
        if int(active or 0) >= max_active:
            raise JobAcceptanceError(AcceptanceErrorCode.ACTIVE_LIMIT)
        recent = await session.scalar(
            select(func.count(GenerationJob.id)).where(
                GenerationJob.telegram_user_id == user_id,
                GenerationJob.created_at >= now - timedelta(seconds=window_seconds),
            )
        )
        if int(recent or 0) >= max_recent:
            raise JobAcceptanceError(AcceptanceErrorCode.RECENT_LIMIT)

    async def create(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        submission_uuid: UUID,
        source: SourceSnapshot,
        intent: IntentSnapshot,
        image_model: str,
        prompt_revision: str,
        now: datetime,
    ) -> GenerationJob:
        job = GenerationJob(
            id=uuid4(),
            telegram_user_id=user_id,
            client_submission_uuid=submission_uuid,
            chat_id=source.chat_id,
            source_message_id=source.message_id,
            telegram_file_id=source.file_id,
            telegram_file_unique_id=source.file_unique_id,
            source_media_kind=source.media_kind,
            source_mime_type=source.mime_type,
            source_byte_size=source.byte_size,
            source_width=source.width,
            source_height=source.height,
            intent_kind=intent.kind.value,
            palette_color_id=intent.palette_color_id,
            custom_color_version_id=intent.custom_color_version_id,
            custom_color_sha256=intent.custom_color_sha256,
            intent_display_name=intent.display_name,
            image_model=image_model,
            prompt_revision=prompt_revision,
            status="queued",
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        session.add(JobOutbox(job_id=job.id, created_at=now))
        await session.flush()
        return job
