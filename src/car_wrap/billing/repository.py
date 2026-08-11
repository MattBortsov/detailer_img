"""Row-locked primitives for auditable entitlement mutations."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.billing.catalog import get_payable_product
from car_wrap.billing.contracts import AllowanceKind, LedgerEntryKind, Product
from car_wrap.db.models import AllowanceBalance, BillingLedgerEntry, BillingOrder


class BillingRepository:
    """Keep locking and immutable ledger construction inside one boundary."""

    async def lock_account(self, session: AsyncSession, *, user_id: int) -> None:
        if user_id <= 0:
            raise ValueError("Telegram user ID must be positive")
        await session.execute(select(func.pg_advisory_xact_lock(user_id)))

    async def balance(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        allowance_kind: AllowanceKind,
    ) -> AllowanceBalance | None:
        return cast(
            AllowanceBalance | None,
            await session.scalar(
                select(AllowanceBalance)
                .where(
                    AllowanceBalance.telegram_user_id == user_id,
                    AllowanceBalance.allowance_kind == allowance_kind.value,
                )
                .with_for_update()
            ),
        )

    async def intro_offer_available(
        self, session: AsyncSession, *, user_id: int
    ) -> bool:
        """Return whether fewer than three active intro purchases occupy slots.

        Pending orders reserve capacity until they are definitively rejected or
        reconciled. Failed/cancelled orders remain auditable but release their
        numbered slot for a safe retry.
        """

        occupied = await session.scalar(
            select(func.count(BillingOrder.id)).where(
                BillingOrder.telegram_user_id == user_id,
                BillingOrder.product_id == "intro_25",
                BillingOrder.status.in_(("pending", "confirmed")),
            )
        )
        return int(occupied or 0) < 3

    async def next_intro_number(self, session: AsyncSession, *, user_id: int) -> int:
        """Choose the first free active slot while the caller holds account lock."""

        occupied = set(
            (
                await session.scalars(
                    select(BillingOrder.intro_number).where(
                        BillingOrder.telegram_user_id == user_id,
                        BillingOrder.product_id == "intro_25",
                        BillingOrder.status.in_(("pending", "confirmed")),
                    )
                )
            ).all()
        )
        for number in range(1, 4):
            if number not in occupied:
                return number
        raise ValueError("introductory purchases are exhausted")

    def payable_product(self, product_id: str) -> Product:
        """Resolve price and allowance only from the immutable local catalog."""

        return get_payable_product(product_id)

    async def append_ledger_entry(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        balance_id: UUID,
        allowance_kind: AllowanceKind,
        entry_kind: LedgerEntryKind,
        delta_count: int,
        idempotency_key: str,
        occurred_at: datetime,
        order_id: UUID | None = None,
        reservation_id: UUID | None = None,
        job_id: UUID | None = None,
    ) -> BillingLedgerEntry:
        """Append one immutable balance mutation after callers acquired row locks."""

        if not delta_count:
            raise ValueError("ledger delta must be non-zero")
        entry = BillingLedgerEntry(
            telegram_user_id=user_id,
            balance_id=balance_id,
            allowance_kind=allowance_kind.value,
            entry_kind=entry_kind.value,
            delta_count=delta_count,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            order_id=order_id,
            reservation_id=reservation_id,
            job_id=job_id,
        )
        session.add(entry)
        await session.flush()
        return entry
