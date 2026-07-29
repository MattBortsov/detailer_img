"""Low-concurrency worker coordinator and UUID hint contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from car_wrap.jobs.contracts import ClaimedAttempt, IntentKind
from car_wrap.worker.main import WorkerCoordinator, job_id_from_message
from car_wrap.worker.service import WorkerOutcome

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 7, 29, 14, tzinfo=UTC)


def _attempt(job_id: UUID) -> ClaimedAttempt:
    return ClaimedAttempt(
        job_id=job_id,
        attempt_id=uuid4(),
        attempt_number=1,
        worker_id="worker-1",
        lease_expires_at=NOW + timedelta(minutes=5),
        telegram_user_id=10,
        chat_id=10,
        source_message_id=20,
        telegram_file_id="source",
        source_media_kind="photo",
        source_mime_type="image/jpeg",
        source_byte_size=100,
        source_width=300,
        source_height=300,
        intent_kind=IntentKind.PALETTE,
        intent_display_name="Графитовый",
        palette_color_id="charcoal",
        custom_color_version_id=None,
        custom_color_sha256=None,
        image_model="x-ai/grok-imagine-image-quality",
        prompt_revision="vehicle-wrap-v1",
    )


class Session:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class SessionContext:
    async def __aenter__(self) -> Session:
        return Session()

    async def __aexit__(self, *args: object) -> None:
        return None


class Repository:
    def __init__(self, attempts: list[ClaimedAttempt | None]) -> None:
        self.attempts = attempts
        self.events: list[object] = []

    async def reconcile_expired(self, session: object, **kwargs: Any) -> int:
        self.events.append("reconcile")
        return 0

    async def claim(self, session: object, **kwargs: Any) -> ClaimedAttempt | None:
        self.events.append(("claim", kwargs["job_id"]))
        return self.attempts.pop(0)


class Service:
    def __init__(self) -> None:
        self.jobs: list[UUID] = []
        self.active = 0
        self.maximum_active = 0

    async def execute(self, attempt: ClaimedAttempt) -> WorkerOutcome:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.jobs.append(attempt.job_id)
        self.active -= 1
        return WorkerOutcome(job_id=attempt.job_id, error_code=None)


async def test_poll_reconciles_then_claims_and_processes_one_job() -> None:
    job_id = uuid4()
    repository = Repository([_attempt(job_id)])
    service = Service()
    coordinator = WorkerCoordinator(
        session_factory=SessionContext,
        repository=repository,
        service=service,
        worker_id="worker-1",
        lease_seconds=300,
        clock=lambda: NOW,
    )

    assert await coordinator.run_once() is True

    assert repository.events == ["reconcile", ("claim", None)]
    assert service.jobs == [job_id]
    assert service.maximum_active == 1


async def test_uuid_hint_is_only_a_targeted_timing_hint() -> None:
    job_id = uuid4()
    repository = Repository([None])
    service = Service()
    coordinator = WorkerCoordinator(
        session_factory=SessionContext,
        repository=repository,
        service=service,
        worker_id="worker-1",
        lease_seconds=300,
        clock=lambda: NOW,
    )

    assert await coordinator.run_once(job_id=job_id) is False

    assert repository.events == ["reconcile", ("claim", job_id)]
    assert service.jobs == []


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (None, None),
        ({"type": "subscribe", "data": 1}, None),
        ({"type": "message", "data": b"not-a-uuid"}, None),
        ({"type": "message", "data": 123}, None),
    ],
)
async def test_malformed_or_nonmessage_hints_are_ignored(
    message: dict[str, object] | None,
    expected: None,
) -> None:
    assert job_id_from_message(message) is expected


async def test_canonical_message_yields_only_its_uuid() -> None:
    job_id = uuid4()
    assert job_id_from_message({"type": "message", "data": str(job_id)}) == job_id
