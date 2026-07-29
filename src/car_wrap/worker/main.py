"""Signal-aware sequential generation worker with PostgreSQL authority."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
from aiogram import Bot
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.bot.delivery import TelegramSender
from car_wrap.config import AppSettings
from car_wrap.custom_colors.repository import CustomColorRepository
from car_wrap.custom_colors.storage import FilesystemPrivateStorage
from car_wrap.db.session import create_session_factory
from car_wrap.generation.provider import OpenRouterImagesProvider
from car_wrap.jobs.contracts import ClaimedAttempt
from car_wrap.jobs.relay import canonical_job_id
from car_wrap.jobs.worker_repository import WorkerRepository
from car_wrap.worker.service import GenerationWorkerService

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class WorkerCoordinator:
    """Claim and execute at most one PostgreSQL-authorized job per call."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        repository: WorkerRepository,
        service: GenerationWorkerService,
        worker_id: str,
        lease_seconds: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._repository = repository
        self._service = service
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run_once(self, *, job_id: UUID | None = None) -> bool:
        """Reconcile first, then claim one hinted or oldest queued job."""

        async with self._sessions() as session:
            try:
                now = self._clock()
                await self._repository.reconcile_expired(session, now=now)
                attempt = await self._repository.claim(
                    session,
                    worker_id=self._worker_id,
                    now=now,
                    lease_seconds=self._lease_seconds,
                    job_id=job_id,
                )
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
        if attempt is None:
            return False
        await self._execute_isolated(attempt)
        return True

    async def _execute_isolated(self, attempt: ClaimedAttempt) -> None:
        try:
            outcome = await self._service.execute(attempt)
        except Exception:
            logger.warning(
                "generation_attempt_interrupted",
                extra={
                    "job_id": str(attempt.job_id),
                    "attempt_id": str(attempt.attempt_id),
                    "error_code": "internal_failure",
                },
            )
            return
        logger.info(
            "generation_attempt_finished",
            extra={
                "job_id": str(outcome.job_id),
                "attempt_id": str(attempt.attempt_id),
                "error_code": (
                    outcome.error_code.value if outcome.error_code is not None else None
                ),
            },
        )


def job_id_from_message(message: dict[str, Any] | None) -> UUID | None:
    """Extract a canonical UUID from one Redis Pub/Sub message."""

    if message is None or message.get("type") != "message":
        return None
    payload = message.get("data")
    if not isinstance(payload, (str, bytes)):
        return None
    try:
        return canonical_job_id(payload)
    except ValueError:
        return None


async def run_worker(settings: AppSettings) -> None:
    """Own worker resources and retain PostgreSQL polling when Redis is absent."""

    engine, sessions = create_session_factory(settings.database_url)
    bot = Bot(token=settings.bot_token.get_secret_value())
    provider_client = httpx.AsyncClient()
    redis_client = Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=False,
        socket_connect_timeout=min(settings.job_worker_poll_seconds, 5.0),
        socket_timeout=max(settings.job_worker_poll_seconds, 1.0),
    )
    pubsub = redis_client.pubsub()
    custom_colors = CustomColorRepository(quota=settings.custom_color_quota)
    repository = WorkerRepository(custom_colors)
    storage = FilesystemPrivateStorage(
        settings.custom_color_storage_root,
        max_object_bytes=settings.custom_color_max_bytes,
    )
    service = GenerationWorkerService(
        session_factory=sessions,
        repository=repository,
        downloader=bot,
        storage=storage,
        provider=OpenRouterImagesProvider(provider_client, settings),
        sender=cast(TelegramSender, bot),
        settings=settings,
    )
    coordinator = WorkerCoordinator(
        session_factory=sessions,
        repository=repository,
        service=service,
        worker_id=f"worker:{uuid4().hex}",
        lease_seconds=settings.job_lease_seconds,
    )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(stop_signal, stopping.set)
            installed_signals.append(stop_signal)
        except NotImplementedError:
            pass

    subscribed = False
    try:
        while not stopping.is_set():
            await coordinator.run_once()
            if stopping.is_set():
                break
            if not subscribed:
                try:
                    await pubsub.subscribe(settings.job_wakeup_channel)
                    subscribed = True
                except Exception:
                    logger.warning("generation_hint_channel_unavailable")
            message: dict[str, Any] | None = None
            if subscribed:
                try:
                    raw_message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=settings.job_worker_poll_seconds,
                    )
                    if isinstance(raw_message, dict):
                        message = raw_message
                except Exception:
                    subscribed = False
                    logger.warning("generation_hint_channel_unavailable")
            else:
                try:
                    await asyncio.wait_for(
                        stopping.wait(),
                        timeout=settings.job_worker_poll_seconds,
                    )
                except TimeoutError:
                    pass
            hinted_job_id = job_id_from_message(message)
            if hinted_job_id is not None and not stopping.is_set():
                await coordinator.run_once(job_id=hinted_job_id)
    finally:
        for stop_signal in installed_signals:
            loop.remove_signal_handler(stop_signal)
        with suppress(Exception):
            close_pubsub = cast(Callable[[], Awaitable[None]], pubsub.aclose)
            await close_pubsub()
        with suppress(Exception):
            await redis_client.aclose()
        await provider_client.aclose()
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(run_worker(AppSettings.from_environment()))


if __name__ == "__main__":
    main()
