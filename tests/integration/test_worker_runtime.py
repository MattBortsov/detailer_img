"""PostgreSQL polling fallback and duplicate-hint runtime behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from car_wrap.custom_colors.repository import CustomColorRepository
from car_wrap.db.models import GenerationJob
from car_wrap.jobs.contracts import ClaimedAttempt
from car_wrap.jobs.worker_repository import WorkerRepository
from car_wrap.worker.main import WorkerCoordinator
from car_wrap.worker.service import WorkerOutcome

pytestmark = [pytest.mark.postgresql, pytest.mark.asyncio]
NOW = datetime(2026, 7, 29, 14, tzinfo=UTC)


class RecordingService:
    def __init__(self) -> None:
        self.jobs: list[UUID] = []

    async def execute(self, attempt: ClaimedAttempt) -> WorkerOutcome:
        self.jobs.append(attempt.job_id)
        return WorkerOutcome(job_id=attempt.job_id, error_code=None)


async def test_database_poll_works_without_redis_and_duplicate_hint_is_inert(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    job = GenerationJob(
        id=uuid4(),
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
        intent_kind="palette",
        palette_color_id="charcoal",
        intent_display_name="Графитовый",
        image_model="x-ai/grok-imagine-image-quality",
        prompt_revision="vehicle-wrap-v1",
        status="queued",
        created_at=NOW,
        updated_at=NOW,
    )
    async with sessions() as session:
        session.add(job)
        await session.commit()
    repository = WorkerRepository(CustomColorRepository(quota=20))
    service = RecordingService()
    coordinator = WorkerCoordinator(
        session_factory=sessions,
        repository=repository,
        service=service,
        worker_id="worker-1",
        lease_seconds=300,
        clock=lambda: NOW,
    )

    assert await coordinator.run_once() is True
    assert await coordinator.run_once(job_id=job.id) is False

    assert service.jobs == [job.id]
