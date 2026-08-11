"""PostgreSQL confirmed-renewal lifecycle coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from car_wrap.billing.contracts import LedgerEntryKind
from car_wrap.billing.payments import PaymentService
from car_wrap.billing.tbank import (
    TBankChargeResult,
    TBankInitResult,
    canonical_token,
)
from car_wrap.db.models import (
    AllowanceBalance,
    BillingLedgerEntry,
    BillingOrder,
    Subscription,
    TelegramUser,
)

pytestmark = [pytest.mark.postgresql, pytest.mark.asyncio]


class _SuccessfulRenewal:
    terminal_key = "terminal"
    webhook_password = "password"  # noqa: S105 - isolated signing fixture

    async def init_payment(self, **_: object) -> TBankInitResult:
        return TBankInitResult(payment_id="renewal-payment", payment_url=None)

    async def charge(self, **_: object) -> TBankChargeResult:
        return TBankChargeResult(payment_id="renewal-payment")


async def test_confirmed_renewal_expires_remainder_and_grants_one_new_period(
    database_engine: AsyncEngine,
) -> None:
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    period_start = now - timedelta(days=30)
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        user = TelegramUser(telegram_user_id=3001)
        session.add(user)
        await session.flush()
        subscription = Subscription(
            telegram_user_id=3001,
            product_id="plus",
            status="active",
            provider_rebill_id="rebill-1",
            billing_period_start=period_start,
            billing_period_end=now,
            auto_renew_consent_at=period_start,
            consent_amount_kopecks=49900,
            consent_period_days=30,
        )
        session.add(subscription)
        await session.flush()
        session.add(
            AllowanceBalance(
                telegram_user_id=3001,
                allowance_kind="monthly",
                subscription_id=subscription.id,
                granted_count=30,
                consumed_count=10,
                expires_at=now,
            )
        )
        await session.commit()

    payments = PaymentService(sessions, _SuccessfulRenewal())  # type: ignore[arg-type]
    order = await payments.start_renewal(subscription_id=subscription.id, now=now)
    assert order is not None

    payload: dict[str, object] = {
        "TerminalKey": "terminal",
        "PaymentId": "renewal-payment",
        "OrderId": f"cw-{order.id}",
        "Amount": 49900,
        "Status": "CONFIRMED",
        "Currency": "RUB",
    }
    payload["Token"] = canonical_token(payload, "password")
    assert await payments.confirm_webhook(payload)
    assert not await payments.confirm_webhook(payload)

    async with sessions() as session:
        renewed = await session.get(Subscription, subscription.id)
        balance = await session.scalar(
            select(AllowanceBalance).where(
                AllowanceBalance.subscription_id == subscription.id
            )
        )
        stored_order = await session.get(BillingOrder, order.id)
        entries = list((await session.scalars(select(BillingLedgerEntry))).all())
    assert renewed is not None
    assert renewed.billing_period_start > now
    assert renewed.billing_period_end - renewed.billing_period_start == timedelta(
        days=30
    )
    assert renewed.renewal_failure_count == 0
    assert balance is not None
    assert (balance.granted_count, balance.consumed_count) == (40, 10)
    assert balance.granted_count - balance.consumed_count == 30
    assert stored_order is not None and stored_order.status == "confirmed"
    assert [entry.entry_kind for entry in entries] == [
        LedgerEntryKind.EXPIRE.value,
        LedgerEntryKind.GRANT.value,
    ]
    assert [entry.delta_count for entry in entries] == [-20, 30]
