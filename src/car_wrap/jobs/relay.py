"""Restart-safe PostgreSQL outbox relay with UUID-only Redis hints."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from car_wrap.db.models import JobOutbox

logger = logging.getLogger(__name__)


class JobHintPublisher(Protocol):
    """Small boundary implemented by ``redis.asyncio.Redis``."""

    def publish(self, channel: Any, message: Any) -> Awaitable[int]: ...


def canonical_job_id(payload: str | bytes) -> UUID:
    """Accept only the lowercase hyphenated UUID representation."""

    try:
        decoded = payload.decode("ascii") if isinstance(payload, bytes) else payload
        job_id = UUID(decoded)
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("job hint must be a canonical UUID") from error
    if decoded != str(job_id):
        raise ValueError("job hint must be a canonical UUID")
    return job_id


def job_hint(job_id: UUID) -> str:
    """Build the complete Redis payload without private job metadata."""

    payload = str(job_id)
    canonical_job_id(payload)
    return payload


class JobOutboxRelay:
    """Publish bounded pending outbox rows and persist successful attempts."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: JobHintPublisher,
        channel: str,
        batch_size: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._publisher = publisher
        self._channel = channel
        self._batch_size = batch_size
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run_once(self) -> int:
        """Relay one ordered batch; failed rows remain authoritative and pending."""

        async with self._sessions() as session, session.begin():
            pending = (
                await session.scalars(
                    select(JobOutbox)
                    .where(JobOutbox.published_at.is_(None))
                    .order_by(JobOutbox.created_at, JobOutbox.job_id)
                    .limit(self._batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            published = 0
            for row in pending:
                attempted_at = self._clock()
                try:
                    await self._publisher.publish(
                        self._channel,
                        job_hint(row.job_id),
                    )
                except Exception:  # Redis clients expose multiple transport errors.
                    logger.warning(
                        "job_outbox_publish_failed",
                        extra={"job_id": str(row.job_id)},
                    )
                    continue
                row.publish_attempts += 1
                row.last_attempt_at = attempted_at
                row.published_at = attempted_at
                published += 1
            return published
