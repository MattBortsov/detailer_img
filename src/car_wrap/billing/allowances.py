"""Transactional allowance reservation and terminal settlement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.billing.contracts import AllowanceKind, LedgerEntryKind
from car_wrap.billing.repository import BillingRepository
from car_wrap.db.models import (
    AllowanceBalance,
    AllowanceReservation,
    Subscription,
    TelegramUser,
)


class AllowanceUnavailable(ValueError):
    """No delivered-generation entitlement is currently available."""

    code = "allowance_required"


@dataclass(frozen=True, slots=True)
class AllowanceCandidate:
    kind: AllowanceKind
    available: int
    expires_at: datetime | None = None


_PRIORITY = (
    AllowanceKind.FREE,
    AllowanceKind.INTRO,
    AllowanceKind.PACKAGE,
    AllowanceKind.BONUS,
    AllowanceKind.MONTHLY,
)


def select_candidate(
    candidates: list[AllowanceCandidate], *, now: datetime
) -> AllowanceCandidate:
    """Pick one spendable balance using the documented deterministic order."""

    for kind in _PRIORITY:
        for candidate in candidates:
            if (
                candidate.kind is kind
                and candidate.available > 0
                and (candidate.expires_at is None or candidate.expires_at > now)
            ):
                return candidate
    raise AllowanceUnavailable(AllowanceUnavailable.code)


class AllowanceService:
    """Own row-locked allocation and its receipt/terminal settlement."""

    def __init__(self, repository: BillingRepository | None = None) -> None:
        self._repository = repository or BillingRepository()

    async def reserve(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        job_id: UUID,
        now: datetime,
    ) -> AllowanceReservation:
        """Reserve exactly one entitlement for a newly-created immutable job."""

        await self._repository.lock_account(session, user_id=user_id)
        existing = cast(
            AllowanceReservation | None,
            await session.scalar(
                select(AllowanceReservation)
                .where(AllowanceReservation.job_id == job_id)
                .with_for_update()
            ),
        )
        if existing is not None:
            return existing
        await self._ensure_account(session, user_id=user_id, now=now)
        balances = await self._locked_candidates(session, user_id=user_id, now=now)
        selected = select_candidate(
            [
                AllowanceCandidate(
                    kind=AllowanceKind(balance.allowance_kind),
                    available=(
                        balance.granted_count
                        - balance.reserved_count
                        - balance.consumed_count
                    ),
                    expires_at=balance.expires_at,
                )
                for balance in balances
            ],
            now=now,
        )
        balance = next(
            balance
            for balance in balances
            if AllowanceKind(balance.allowance_kind) is selected.kind
            and balance.granted_count - balance.reserved_count - balance.consumed_count
            > 0
            and (balance.expires_at is None or balance.expires_at > now)
        )
        reservation = AllowanceReservation(
            id=uuid4(),
            balance_id=balance.id,
            job_id=job_id,
            status="reserved",
            reserved_at=now,
        )
        balance.reserved_count += 1
        session.add(reservation)
        await session.flush()
        await self._repository.append_ledger_entry(
            session,
            user_id=user_id,
            balance_id=balance.id,
            allowance_kind=selected.kind,
            entry_kind=LedgerEntryKind.RESERVE,
            delta_count=-1,
            idempotency_key=f"reservation:{reservation.id}:reserve",
            reservation_id=reservation.id,
            job_id=job_id,
            occurred_at=now,
        )
        return reservation

    async def consume_after_receipt(
        self, session: AsyncSession, *, job_id: UUID, now: datetime
    ) -> None:
        """Consume only the reservation bound to a committed Telegram receipt."""

        reservation, balance = await self._locked_reservation(session, job_id=job_id)
        if reservation is None or balance is None or reservation.status != "reserved":
            return
        reservation.status = "consumed"
        reservation.terminal_at = now
        balance.reserved_count -= 1
        balance.consumed_count += 1
        await self._repository.append_ledger_entry(
            session,
            user_id=balance.telegram_user_id,
            balance_id=balance.id,
            allowance_kind=AllowanceKind(balance.allowance_kind),
            entry_kind=LedgerEntryKind.CONSUME,
            delta_count=-1,
            idempotency_key=f"reservation:{reservation.id}:consume",
            reservation_id=reservation.id,
            job_id=job_id,
            occurred_at=now,
        )

    async def release_terminal(
        self, session: AsyncSession, *, job_id: UUID, now: datetime
    ) -> None:
        """Release a failed or ambiguous terminal reservation exactly once."""

        reservation, balance = await self._locked_reservation(session, job_id=job_id)
        if reservation is None or balance is None or reservation.status != "reserved":
            return
        reservation.status = "released"
        reservation.terminal_at = now
        balance.reserved_count -= 1
        await self._repository.append_ledger_entry(
            session,
            user_id=balance.telegram_user_id,
            balance_id=balance.id,
            allowance_kind=AllowanceKind(balance.allowance_kind),
            entry_kind=LedgerEntryKind.RELEASE,
            delta_count=1,
            idempotency_key=f"reservation:{reservation.id}:release",
            reservation_id=reservation.id,
            job_id=job_id,
            occurred_at=now,
        )

    async def _ensure_account(
        self, session: AsyncSession, *, user_id: int, now: datetime
    ) -> None:
        account = await session.get(TelegramUser, user_id)
        if account is None:
            session.add(
                TelegramUser(
                    telegram_user_id=user_id, first_seen_at=now, last_seen_at=now
                )
            )
            await session.flush()
        free = await self._repository.balance(
            session, user_id=user_id, allowance_kind=AllowanceKind.FREE
        )
        if free is not None:
            return
        free = AllowanceBalance(
            telegram_user_id=user_id,
            allowance_kind=AllowanceKind.FREE.value,
            granted_count=1,
        )
        session.add(free)
        await session.flush()
        await self._repository.append_ledger_entry(
            session,
            user_id=user_id,
            balance_id=free.id,
            allowance_kind=AllowanceKind.FREE,
            entry_kind=LedgerEntryKind.GRANT,
            delta_count=1,
            idempotency_key=f"account:{user_id}:free-grant",
            occurred_at=now,
        )

    async def _locked_candidates(
        self, session: AsyncSession, *, user_id: int, now: datetime
    ) -> list[AllowanceBalance]:
        balances: list[AllowanceBalance] = []
        for kind in _PRIORITY[:-1]:
            balance = await self._repository.balance(
                session, user_id=user_id, allowance_kind=kind
            )
            if balance is not None:
                balances.append(balance)
        monthly = (
            await session.scalars(
                select(AllowanceBalance)
                .join(Subscription, AllowanceBalance.subscription_id == Subscription.id)
                .where(
                    AllowanceBalance.telegram_user_id == user_id,
                    AllowanceBalance.allowance_kind == AllowanceKind.MONTHLY.value,
                    AllowanceBalance.expires_at > now,
                    Subscription.status == "active",
                    Subscription.billing_period_end > now,
                )
                .order_by(AllowanceBalance.expires_at, AllowanceBalance.id)
                .with_for_update()
            )
        ).all()
        return [*balances, *monthly]

    async def _locked_reservation(
        self, session: AsyncSession, *, job_id: UUID
    ) -> tuple[AllowanceReservation | None, AllowanceBalance | None]:
        reservation = cast(
            AllowanceReservation | None,
            await session.scalar(
                select(AllowanceReservation)
                .where(AllowanceReservation.job_id == job_id)
                .with_for_update()
            ),
        )
        if reservation is None:
            return None, None
        balance = cast(
            AllowanceBalance | None,
            await session.scalar(
                select(AllowanceBalance)
                .where(AllowanceBalance.id == reservation.balance_id)
                .with_for_update()
            ),
        )
        return reservation, balance
