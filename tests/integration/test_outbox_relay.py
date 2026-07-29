"""Real PostgreSQL outbox relay behavior and optional real Redis boundary."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from car_wrap.db.models import GenerationJob, JobOutbox
from car_wrap.jobs.relay import JobOutboxRelay, canonical_job_id

pytestmark = [pytest.mark.postgresql, pytest.mark.asyncio]
NOW = datetime(2026, 7, 29, 7, 30, tzinfo=UTC)


class RecordingPublisher:
    def __init__(self, *, fail: bool = False, delay: float = 0) -> None:
        self.fail = fail
        self.delay = delay
        self.messages: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise ConnectionError
        self.messages.append((channel, message))
        return 1


async def seed_pending_job(
    sessions: async_sessionmaker[AsyncSession],
    *,
    job_id: UUID | None = None,
) -> UUID:
    selected_job_id = job_id or uuid4()
    async with sessions() as session:
        session.add(
            GenerationJob(
                id=selected_job_id,
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
        )
        session.add(JobOutbox(job_id=selected_job_id, created_at=NOW))
        await session.commit()
    return selected_job_id


def relay(
    sessions: async_sessionmaker[AsyncSession],
    publisher: RecordingPublisher | Redis,
) -> JobOutboxRelay:
    return JobOutboxRelay(
        session_factory=sessions,
        publisher=publisher,
        channel="car-wrap.jobs",
        batch_size=10,
        clock=lambda: NOW,
    )


async def test_success_publishes_uuid_and_marks_row(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    job_id = await seed_pending_job(sessions)
    publisher = RecordingPublisher()

    assert await relay(sessions, publisher).run_once() == 1

    assert publisher.messages == [("car-wrap.jobs", str(job_id))]
    async with sessions() as session:
        row = await session.get(JobOutbox, job_id)
    assert row is not None
    assert (row.publish_attempts, row.last_attempt_at, row.published_at) == (
        1,
        NOW,
        NOW,
    )


async def test_failure_leaves_authoritative_row_pending(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    job_id = await seed_pending_job(sessions)

    assert await relay(sessions, RecordingPublisher(fail=True)).run_once() == 0

    async with sessions() as session:
        row = await session.get(JobOutbox, job_id)
    assert row is not None
    assert (row.publish_attempts, row.last_attempt_at, row.published_at) == (
        0,
        None,
        None,
    )


async def test_two_relays_do_not_publish_same_locked_row(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    job_id = await seed_pending_job(sessions)
    publisher = RecordingPublisher(delay=0.05)

    counts = await asyncio.gather(
        relay(sessions, publisher).run_once(),
        relay(sessions, publisher).run_once(),
    )

    assert sorted(counts) == [0, 1]
    assert publisher.messages == [("car-wrap.jobs", str(job_id))]


async def test_new_relay_publishes_preexisting_pending_row(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    job_id = await seed_pending_job(sessions)
    publisher = RecordingPublisher()

    restarted_relay = relay(sessions, publisher)

    assert await restarted_relay.run_once() == 1
    assert publisher.messages == [("car-wrap.jobs", str(job_id))]


async def test_real_redis_receives_uuid_only_hint(
    database_engine: AsyncEngine,
) -> None:
    redis_url = os.environ.get("CAR_WRAP_TEST_REDIS_URL")
    if redis_url is None:
        pytest.skip("CAR_WRAP_TEST_REDIS_URL is required")
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    job_id = await seed_pending_job(sessions)
    redis_client = Redis.from_url(redis_url, decode_responses=False)
    pubsub = redis_client.pubsub()
    try:
        await pubsub.subscribe("car-wrap.jobs")
        await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)

        assert await relay(sessions, redis_client).run_once() == 1

        message = await pubsub.get_message(
            ignore_subscribe_messages=True,
            timeout=1,
        )
        assert message is not None
        assert canonical_job_id(message["data"]) == job_id
    finally:
        await pubsub.aclose()
        await redis_client.aclose()
