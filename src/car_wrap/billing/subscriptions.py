"""Bounded, consented recurring-renewal orchestration."""
# ruff: noqa: RUF001

from __future__ import annotations

from datetime import UTC, datetime

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from car_wrap.billing.payments import PaymentService
from car_wrap.db.models import Subscription

RENEWAL_FAILURE_COPY = (
    "Не удалось продлить подписку. Автосписание остановлено; "
    "пакеты генераций сохранены."
)


class SubscriptionService:
    """Initiate only due consented renewals; webhooks grant the replacement quota."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        payments: PaymentService,
    ) -> None:
        self._session_factory = session_factory
        self._payments = payments

    async def scan_due(self, bot: Bot, *, now: datetime | None = None) -> int:
        """Start at most one safe charge per eligible expired subscription."""

        current = now or datetime.now(UTC)
        async with self._session_factory() as session:
            candidates = list(
                (
                    await session.scalars(
                        select(Subscription.id).where(
                            Subscription.status == "active",
                            Subscription.billing_period_end <= current,
                            Subscription.robokassa_parent_invoice_id.is_not(None),
                            Subscription.cancelled_at.is_(None),
                            Subscription.auto_renew_consent_at.is_not(None),
                            Subscription.renewal_failure_count < 3,
                        )
                    )
                ).all()
            )
        started = 0
        for subscription_id in candidates:
            try:
                order = await self._payments.start_renewal(
                    subscription_id=subscription_id, now=current
                )
            except Exception:
                await self._notify_terminal_failure(bot, subscription_id)
                continue
            if order is not None:
                started += 1
        return started

    async def _notify_terminal_failure(self, bot: Bot, subscription_id: object) -> None:
        async with self._session_factory() as session:
            subscription = await session.get(Subscription, subscription_id)
            if subscription is None or subscription.status != "past_due":
                return
            try:
                await bot.send_message(
                    subscription.telegram_user_id, RENEWAL_FAILURE_COPY
                )
            except Exception:
                return
