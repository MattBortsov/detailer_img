"""PostgreSQL introductory-purchase lifecycle and retry invariants."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from car_wrap.billing.payments import PaymentConfirmationError, PaymentService
from car_wrap.billing.repository import BillingRepository
from car_wrap.billing.tbank import (
    TBankInitResult,
    TBankRequestNotSent,
    canonical_token,
)
from car_wrap.db.models import AllowanceBalance, BillingOrder, TelegramUser

pytestmark = [pytest.mark.postgresql, pytest.mark.asyncio]


class _SequencedInit:
    terminal_key = "terminal"
    webhook_password = "password"  # noqa: S105 - isolated signing fixture

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def init_payment(self, **_: object) -> TBankInitResult:
        self.calls += 1
        if self.calls <= self.failures:
            raise TBankRequestNotSent
        return TBankInitResult(
            payment_id=f"provider-{self.calls}",
            payment_url=f"https://pay.example/{self.calls}",
        )


def _confirmation(provider_order_id: str, payment_id: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "TerminalKey": "terminal",
        "PaymentId": payment_id,
        "OrderId": provider_order_id,
        "Amount": 2500,
        "Status": "CONFIRMED",
        "Currency": "RUB",
    }
    payload["Token"] = canonical_token(payload, "password")
    return payload


async def test_failed_init_retries_then_three_confirmations_exhaust_intro_offer(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(TelegramUser(telegram_user_id=2001))
        await session.commit()

    tbank = _SequencedInit(failures=3)
    payments = PaymentService(sessions, tbank)  # type: ignore[arg-type]
    for attempt in range(3):
        with pytest.raises(TBankRequestNotSent):
            await payments.start_checkout(
                user_id=2001,
                product_id="intro_25",
                idempotency_key=f"failed-{attempt}",
            )

    async with sessions() as session:
        failed = list(
            (
                await session.scalars(
                    select(BillingOrder).where(BillingOrder.status == "failed")
                )
            ).all()
        )
        assert [order.intro_number for order in failed] == [1, 1, 1]
        assert await BillingRepository().intro_offer_available(session, user_id=2001)

    for slot in range(1, 4):
        order, url = await payments.start_checkout(
            user_id=2001,
            product_id="intro_25",
            idempotency_key=f"successful-{slot}",
        )
        assert order.intro_number == slot
        assert url.startswith("https://pay.example/")
        assert await payments.confirm_webhook(
            _confirmation(f"cw-{order.id}", f"provider-{tbank.calls}")
        )

    async with sessions() as session:
        balance = await session.scalar(
            select(AllowanceBalance).where(
                AllowanceBalance.telegram_user_id == 2001,
                AllowanceBalance.allowance_kind == "intro",
            )
        )
        assert balance is not None
        assert (balance.granted_count, balance.consumed_count) == (3, 0)
        assert not await BillingRepository().intro_offer_available(
            session, user_id=2001
        )

    calls_before_exhausted_checkout = tbank.calls
    with pytest.raises(PaymentConfirmationError):
        await payments.start_checkout(
            user_id=2001,
            product_id="intro_25",
            idempotency_key="must-not-upload",
        )
    assert tbank.calls == calls_before_exhausted_checkout


async def test_concurrent_intro_checkouts_cannot_allocate_a_fourth_active_slot(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(TelegramUser(telegram_user_id=2002))
        await session.commit()

    tbank = _SequencedInit(failures=0)
    payments = PaymentService(sessions, tbank)  # type: ignore[arg-type]
    results = await asyncio.gather(
        *(
            payments.start_checkout(
                user_id=2002,
                product_id="intro_25",
                idempotency_key=f"concurrent-{attempt}",
            )
            for attempt in range(4)
        ),
        return_exceptions=True,
    )

    successes = [result for result in results if isinstance(result, tuple)]
    failures = [
        result for result in results if isinstance(result, PaymentConfirmationError)
    ]
    assert len(successes) == 3
    assert len(failures) == 1
    async with sessions() as session:
        active_numbers = list(
            (
                await session.scalars(
                    select(BillingOrder.intro_number)
                    .where(
                        BillingOrder.telegram_user_id == 2002,
                        BillingOrder.status == "pending",
                    )
                    .order_by(BillingOrder.intro_number)
                )
            ).all()
        )
    assert active_numbers == [1, 2, 3]
