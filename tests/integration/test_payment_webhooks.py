"""PostgreSQL-only payment confirmation idempotency coverage."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from car_wrap.billing.payments import PaymentService
from car_wrap.billing.tbank import TBankOutcomeAmbiguous, canonical_token
from car_wrap.db.models import (
    AllowanceBalance,
    BillingOrder,
    TBankPayment,
    TelegramUser,
)

pytestmark = pytest.mark.postgresql


def test_payment_confirmation_schema_has_durable_correlation_fields() -> None:
    """Keep useful schema assertions runnable without the PostgreSQL fixture."""

    assert BillingOrder.__tablename__ == "billing_orders"
    assert TBankPayment.__tablename__ == "tbank_payments"
    assert TBankPayment.provider_order_id.property.columns[0].nullable is False
    assert TBankPayment.provider_payment_id.property.columns[0].nullable is True


class _TimeoutAfterProviderAcceptedInit:
    terminal_key = "terminal"
    webhook_password = "password"  # noqa: S105 - isolated test signing fixture

    async def init_payment(self, **_: object) -> None:
        # TBank accepted the uploaded Init, but its reply timed out locally.
        raise TBankOutcomeAmbiguous


@pytest.mark.asyncio
async def test_webhook_recovers_ambiguous_init_timeout_exactly_once(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(TelegramUser(telegram_user_id=1001))
        await session.commit()

    payments = PaymentService(sessions, _TimeoutAfterProviderAcceptedInit())  # type: ignore[arg-type]
    with pytest.raises(TBankOutcomeAmbiguous):
        await payments.start_checkout(
            user_id=1001,
            product_id="intro_25",
            idempotency_key="interrupted-init",
        )

    async with sessions() as session:
        payment = await session.scalar(select(TBankPayment))
        order = await session.scalar(select(BillingOrder))
        assert payment is not None
        assert order is not None
        assert payment.provider_payment_id is None
        assert payment.status == "ambiguous"
        assert order.intro_number == 1
        provider_order_id = payment.provider_order_id

    payload = {
        "TerminalKey": "terminal",
        "PaymentId": "provider-payment-1",
        "OrderId": provider_order_id,
        "Amount": 2500,
        "Status": "CONFIRMED",
        "Currency": "RUB",
    }
    payload["Token"] = canonical_token(payload, "password")
    assert await payments.confirm_webhook(payload)
    assert not await payments.confirm_webhook(payload)

    async with sessions() as session:
        payment = await session.scalar(select(TBankPayment))
        order = await session.scalar(select(BillingOrder))
        balance = await session.scalar(select(AllowanceBalance))
        assert payment is not None and payment.status == "confirmed"
        assert payment.provider_payment_id == "provider-payment-1"
        assert order is not None and order.status == "confirmed"
        assert balance is not None and balance.granted_count == 1
