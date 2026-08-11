"""Bot-process lifecycle helpers for the bounded subscription scan."""

from __future__ import annotations

import asyncio

from aiogram import Bot

from car_wrap.billing.subscriptions import SubscriptionService


async def run_subscription_scanner(
    service: SubscriptionService,
    bot: Bot,
    *,
    interval_seconds: float,
    stop: asyncio.Event,
) -> None:
    """Poll one bounded scan at a time and allow the bot lifecycle to stop cleanly."""

    while not stop.is_set():
        await service.scan_due(bot)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


def start_subscription_scanner(
    service: SubscriptionService, bot: Bot, *, interval_seconds: float
) -> tuple[asyncio.Event, asyncio.Task[None]]:
    """Return explicit shutdown ownership; no extra client/session exists."""

    stopped = asyncio.Event()
    task = asyncio.create_task(
        run_subscription_scanner(
            service, bot, interval_seconds=interval_seconds, stop=stopped
        )
    )
    return stopped, task
