"""Dedicated restart-safe outbox relay process."""

from __future__ import annotations

import asyncio
import signal

from redis.asyncio import Redis

from car_wrap.config import AppSettings
from car_wrap.db.session import create_session_factory
from car_wrap.jobs.relay import JobOutboxRelay


async def run_relay(settings: AppSettings) -> None:
    """Drain at startup, poll at a bounded cadence, and own all resources."""

    engine, sessions = create_session_factory(settings.database_url)
    redis_client = Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
    )
    relay = JobOutboxRelay(
        session_factory=sessions,
        publisher=redis_client,
        channel=settings.job_wakeup_channel,
        batch_size=settings.job_relay_batch_size,
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
    try:
        while not stopping.is_set():
            await relay.run_once()
            try:
                await asyncio.wait_for(
                    stopping.wait(),
                    timeout=settings.job_relay_poll_seconds,
                )
            except TimeoutError:
                pass
    finally:
        for stop_signal in installed_signals:
            loop.remove_signal_handler(stop_signal)
        await redis_client.aclose()
        await engine.dispose()


def main() -> None:
    asyncio.run(run_relay(AppSettings.from_environment()))


if __name__ == "__main__":
    main()
