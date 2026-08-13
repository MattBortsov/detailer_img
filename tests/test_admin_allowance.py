"""Administrator generation allowance behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from car_wrap.jobs.contracts import JobStatus
from car_wrap.jobs.service import JobAcceptanceService


def _source() -> SimpleNamespace:
    return SimpleNamespace(
        chat_id=715709681,
        source_message_id=10,
        telegram_file_id="file-id",
        telegram_file_unique_id="unique-id",
        media_kind="photo",
        mime_type="image/jpeg",
        byte_size=1024,
        width=1200,
        height=800,
    )


def _service(*, admin_ids: tuple[int, ...]) -> tuple[JobAcceptanceService, AsyncMock]:
    job_id = uuid4()
    repository = AsyncMock()
    repository.existing.return_value = None
    repository.source.return_value = _source()
    repository.create.return_value = SimpleNamespace(id=job_id)
    allowances = AsyncMock()
    service = JobAcceptanceService(
        repository=repository,
        custom_colors=AsyncMock(),
        image_model="test-model",
        prompt_revision="test-revision",
        max_active=1,
        max_recent=10,
        window_seconds=3600,
        allowance_exempt_user_ids=admin_ids,
        allowances=allowances,
    )
    return service, allowances


@pytest.mark.asyncio
async def test_admin_generation_does_not_reserve_allowance() -> None:
    service, allowances = _service(admin_ids=(715709681,))
    session = AsyncMock()

    accepted = await service.accept(
        session,
        user_id=715709681,
        color_id="charcoal",
        submission_uuid=uuid4(),
    )

    assert accepted.status is JobStatus.QUEUED
    allowances.reserve.assert_not_awaited()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_regular_user_still_reserves_allowance() -> None:
    service, allowances = _service(admin_ids=(715709681,))
    session = AsyncMock()

    await service.accept(
        session,
        user_id=123,
        color_id="charcoal",
        submission_uuid=uuid4(),
    )

    allowances.reserve.assert_awaited_once()
