"""Stored-order checkout and transactional T-Bank confirmation orchestration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from car_wrap.billing.catalog import get_payable_product
from car_wrap.billing.contracts import AllowanceKind, LedgerEntryKind, ProductKind
from car_wrap.billing.repository import BillingRepository
from car_wrap.billing.tbank import (
    PaymentActivationDenied,
    TBankClient,
    TBankInitRejected,
    TBankRequestNotSent,
    verify_notification_token,
)
from car_wrap.db.models import (
    AllowanceBalance,
    BillingOrder,
    Subscription,
    TBankPayment,
)


class PaymentConfirmationError(ValueError):
    """A public callback did not match a stored T-Bank commercial intent."""


class PaymentService:
    """Keep provider data as bounded metadata and grant only in one transaction."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        tbank: TBankClient,
        repository: BillingRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._tbank = tbank
        self._repository = repository or BillingRepository()

    async def start_checkout(
        self,
        *,
        user_id: int,
        product_id: str,
        idempotency_key: str,
        recurring_consent_at: datetime | None = None,
    ) -> tuple[BillingOrder, str]:
        """Persist a random order before Init with catalog-owned money terms."""

        product = get_payable_product(product_id)
        amount_kopecks = product.amount_kopecks
        if amount_kopecks is None:
            raise PaymentConfirmationError
        async with self._session_factory() as session:
            async with session.begin():
                await self._repository.lock_account(session, user_id=user_id)
                intro_number = await self._next_intro_number(
                    session, user_id=user_id, product_kind=product.kind
                )
                order = BillingOrder(
                    telegram_user_id=user_id,
                    product_id=product.id.value,
                    amount_kopecks=amount_kopecks,
                    intro_number=intro_number,
                    idempotency_key=idempotency_key,
                    recurring_consent_at=recurring_consent_at,
                )
                session.add(order)
                await session.flush()
                provider_order_id = f"cw-{order.id}"
                # This row is intentionally written before Init. A signed
                # webhook can therefore recover even if the process dies
                # between T-Bank accepting the intent and receiving its reply.
                session.add(
                    TBankPayment(
                        order_id=order.id,
                        provider_payment_id=None,
                        provider_order_id=provider_order_id,
                        status="initializing",
                    )
                )
        try:
            initialized = await self._tbank.init_payment(
                order_id=provider_order_id,
                amount_kopecks=amount_kopecks,
                description=f"Car Wrap: {product.id.value}",
                customer_key=str(user_id),
                recurrent=product.kind is ProductKind.MONTHLY,
            )
        except (PaymentActivationDenied, TBankRequestNotSent, TBankInitRejected):
            await self._mark_init_failed(provider_order_id)
            raise
        except Exception:
            await self._mark_init_ambiguous(provider_order_id)
            raise
        if initialized.payment_url is None:
            await self._mark_init_ambiguous(provider_order_id)
            raise PaymentConfirmationError("T-Bank Init did not return a checkout URL")
        async with self._session_factory() as session:
            async with session.begin():
                payment = await self._payment_for_provider_order(
                    session, provider_order_id=provider_order_id
                )
                if payment is None:
                    raise PaymentConfirmationError(
                        "missing persisted payment correlation"
                    )
                if payment.provider_payment_id not in (None, initialized.payment_id):
                    raise PaymentConfirmationError("provider payment ID mismatch")
                payment.provider_payment_id = initialized.payment_id
                if payment.status == "initializing":
                    payment.status = "new"
        return order, initialized.payment_url

    async def start_renewal(
        self, *, subscription_id: UUID, now: datetime
    ) -> BillingOrder | None:
        """Claim one due, consented period and ask T-Bank to charge its RebillId.

        The unique ``subscription_id``/period pair is the durable claim.  A
        scheduler restart therefore observes the existing order instead of
        opening another charge for the same monthly period.
        """

        initialized_payment_id: str | None = None
        needs_init = False
        rebill_id: str | None = None
        async with self._session_factory() as session:
            async with session.begin():
                subscription = await session.scalar(
                    select(Subscription)
                    .where(Subscription.id == subscription_id)
                    .with_for_update()
                )
                if not self._eligible_renewal(subscription, now):
                    return None
                if subscription is None:
                    return None
                existing = await session.scalar(
                    select(BillingOrder).where(
                        BillingOrder.subscription_id == subscription.id,
                        BillingOrder.renewal_period_start
                        == subscription.billing_period_start,
                    )
                )
                if existing is not None:
                    payment = await session.scalar(
                        select(TBankPayment)
                        .where(TBankPayment.order_id == existing.id)
                        .with_for_update()
                    )
                    if payment is None or payment.status != "new":
                        return None
                    order = existing
                    provider_order_id = payment.provider_order_id
                    initialized_payment_id = payment.provider_payment_id
                    if initialized_payment_id is None:
                        return None
                else:
                    product = get_payable_product(subscription.product_id)
                    order = BillingOrder(
                        telegram_user_id=subscription.telegram_user_id,
                        product_id=product.id.value,
                        amount_kopecks=product.amount_kopecks,
                        idempotency_key=f"renewal:{uuid4().hex}",
                        subscription_id=subscription.id,
                        renewal_period_start=subscription.billing_period_start,
                    )
                    subscription.renewal_attempted_at = now
                    session.add(order)
                    await session.flush()
                    provider_order_id = f"cw-{order.id}"
                    session.add(
                        TBankPayment(
                            order_id=order.id,
                            provider_payment_id=None,
                            provider_order_id=provider_order_id,
                            status="initializing",
                        )
                    )
                    needs_init = True
                rebill_id = subscription.provider_rebill_id

        try:
            if needs_init:
                initialized = await self._tbank.init_payment(
                    order_id=provider_order_id,
                    amount_kopecks=order.amount_kopecks,
                    description=f"Car Wrap renewal: {order.product_id}",
                    customer_key=str(order.telegram_user_id),
                    recurrent=True,
                    operation_initiator_type="R",
                )
                initialized_payment_id = initialized.payment_id
                await self._record_initialized_payment(
                    provider_order_id=provider_order_id,
                    provider_payment_id=initialized_payment_id,
                )
            if rebill_id is None:
                raise PaymentConfirmationError
            if initialized_payment_id is None:
                raise PaymentConfirmationError("missing initialized payment ID")
            charged = await self._tbank.charge(
                payment_id=initialized_payment_id, rebill_id=rebill_id
            )
        except (PaymentActivationDenied, TBankRequestNotSent, TBankInitRejected):
            await self._mark_init_failed(provider_order_id)
            await self.mark_renewal_failed(subscription_id=subscription_id, now=now)
            raise
        except Exception:
            await self._mark_init_ambiguous(provider_order_id)
            raise

        async with self._session_factory() as session:
            async with session.begin():
                payment = await self._payment_for_provider_order(
                    session, provider_order_id=provider_order_id
                )
                if payment is None:
                    raise PaymentConfirmationError(
                        "missing persisted payment correlation"
                    )
                if payment.provider_payment_id not in (
                    None,
                    initialized_payment_id,
                    charged.payment_id,
                ):
                    raise PaymentConfirmationError("provider payment ID mismatch")
                payment.provider_payment_id = charged.payment_id
                if payment.status == "initializing":
                    payment.status = "new"
        return order

    async def _record_initialized_payment(
        self, *, provider_order_id: str, provider_payment_id: str
    ) -> None:
        """Make a recurrent Init result durable before the subsequent Charge."""

        async with self._session_factory() as session:
            async with session.begin():
                payment = await self._payment_for_provider_order(
                    session, provider_order_id=provider_order_id
                )
                if payment is None:
                    raise PaymentConfirmationError(
                        "missing persisted payment correlation"
                    )
                if payment.provider_payment_id not in (None, provider_payment_id):
                    raise PaymentConfirmationError("provider payment ID mismatch")
                payment.provider_payment_id = provider_payment_id
                if payment.status == "initializing":
                    payment.status = "new"

    async def _next_intro_number(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        product_kind: ProductKind,
    ) -> int | None:
        """Allocate the next 1..3 introduction slot under the account lock."""

        if product_kind is not ProductKind.INTRO:
            return None
        try:
            return await self._repository.next_intro_number(session, user_id=user_id)
        except ValueError as exc:
            raise PaymentConfirmationError(
                "introductory purchases are exhausted"
            ) from exc

    async def _payment_for_provider_order(
        self, session: AsyncSession, *, provider_order_id: str
    ) -> TBankPayment | None:
        payment = await session.scalar(
            select(TBankPayment)
            .where(TBankPayment.provider_order_id == provider_order_id)
            .with_for_update()
        )
        return payment if isinstance(payment, TBankPayment) else None

    async def _mark_init_failed(self, provider_order_id: str) -> None:
        """Record a known failed Init without ever deleting audit correlation."""

        async with self._session_factory() as session:
            async with session.begin():
                payment = await self._payment_for_provider_order(
                    session, provider_order_id=provider_order_id
                )
                if payment is None or payment.status == "confirmed":
                    return
                payment.status = "rejected"
                order = await session.get(
                    BillingOrder, payment.order_id, with_for_update=True
                )
                if order is not None and order.status == "pending":
                    order.status = "failed"

    async def _mark_init_ambiguous(self, provider_order_id: str) -> None:
        """Keep correlation and order eligibility for a later signed webhook."""

        async with self._session_factory() as session:
            async with session.begin():
                payment = await self._payment_for_provider_order(
                    session, provider_order_id=provider_order_id
                )
                if payment is not None and payment.status == "initializing":
                    payment.status = "ambiguous"

    async def mark_renewal_failed(
        self, *, subscription_id: UUID, now: datetime
    ) -> None:
        """Record a bounded, truthful retry state without touching packages."""

        async with self._session_factory() as session:
            async with session.begin():
                subscription = await session.scalar(
                    select(Subscription)
                    .where(Subscription.id == subscription_id)
                    .with_for_update()
                )
                if subscription is None or subscription.status == "cancelled":
                    return
                subscription.renewal_failure_count += 1
                subscription.renewal_attempted_at = now
                if subscription.renewal_failure_count >= 3:
                    subscription.status = "past_due"

    @staticmethod
    def _eligible_renewal(subscription: Subscription | None, now: datetime) -> bool:
        if subscription is None:
            return False
        product = get_payable_product(subscription.product_id)
        return bool(
            subscription.status == "active"
            and subscription.provider_rebill_id
            and subscription.auto_renew_consent_at
            and subscription.consent_amount_kopecks == product.amount_kopecks
            and subscription.consent_period_days == 30
            and subscription.billing_period_end <= now
            and (subscription.renewal_failure_count or 0) < 3
        )

    def production_available(self) -> bool:
        """Expose only the fail-closed movement predicate to transport adapters."""

        return self._tbank.production_available

    async def confirm_webhook(self, payload: dict[str, Any]) -> bool:
        """Correlate all trusted scalar facts then append grants exactly once."""

        if not verify_notification_token(payload, self._tbank.webhook_password):
            return False
        payment_id = payload.get("PaymentId")
        order_id = payload.get("OrderId")
        amount = payload.get("Amount")
        if (
            not isinstance(payment_id, str)
            or not isinstance(order_id, str)
            or amount is None
            or isinstance(amount, bool)
        ):
            return False
        try:
            amount_kopecks = int(amount)
        except (TypeError, ValueError):
            return False
        if (
            payload.get("Status") != "CONFIRMED"
            or payload.get("TerminalKey") != self._tbank.terminal_key
            or payload.get("Currency", "RUB") != "RUB"
        ):
            return False
        async with self._session_factory() as session:
            async with session.begin():
                payment = await self._payment_for_provider_order(
                    session, provider_order_id=order_id
                )
                if payment is None or payment.status == "confirmed":
                    return False
                if payment.provider_payment_id not in (None, payment_id):
                    return False
                order = await session.scalar(
                    select(BillingOrder)
                    .where(BillingOrder.id == payment.order_id)
                    .with_for_update()
                )
                if (
                    order is None
                    or order.status != "pending"
                    or order.amount_kopecks != amount_kopecks
                    or order.currency != "RUB"
                ):
                    return False
                product = get_payable_product(order.product_id)
                if product.amount_kopecks != order.amount_kopecks:
                    return False
                await self._repository.lock_account(
                    session, user_id=order.telegram_user_id
                )
                now = datetime.now(UTC)
                payment.provider_payment_id = payment_id
                payment.status = "confirmed"
                payment.confirmed_at = now
                order.status = "confirmed"
                order.confirmed_at = now
                if order.subscription_id is not None:
                    await self._renew_subscription(session, order, product, now)
                else:
                    await self._grant_purchase(
                        session, order, product.kind, product.allowance, now, payload
                    )
        return True

    async def _grant_purchase(
        self,
        session: AsyncSession,
        order: BillingOrder,
        product_kind: ProductKind,
        allowance: int | None,
        now: datetime,
        payload: dict[str, Any],
    ) -> None:
        if allowance is None:
            raise PaymentConfirmationError
        subscription_id: UUID | None = None
        expires_at: datetime | None = None
        allowance_kind = (
            AllowanceKind.INTRO
            if product_kind is ProductKind.INTRO
            else AllowanceKind.PACKAGE
        )
        if product_kind is ProductKind.MONTHLY:
            rebill_id = payload.get("RebillId")
            if not isinstance(rebill_id, str) or not rebill_id or len(rebill_id) > 128:
                raise PaymentConfirmationError
            if order.recurring_consent_at is None:
                raise PaymentConfirmationError
            subscription = Subscription(
                telegram_user_id=order.telegram_user_id,
                product_id=order.product_id,
                status="active",
                provider_rebill_id=rebill_id,
                billing_period_start=now,
                billing_period_end=now + timedelta(days=30),
                auto_renew_consent_at=order.recurring_consent_at,
                consent_amount_kopecks=order.amount_kopecks,
                consent_period_days=30,
            )
            session.add(subscription)
            await session.flush()
            subscription_id = subscription.id
            expires_at = subscription.billing_period_end
            allowance_kind = AllowanceKind.MONTHLY
        balance = (
            None
            if allowance_kind is AllowanceKind.MONTHLY
            else await self._repository.balance(
                session,
                user_id=order.telegram_user_id,
                allowance_kind=allowance_kind,
            )
        )
        if balance is None:
            balance = AllowanceBalance(
                telegram_user_id=order.telegram_user_id,
                allowance_kind=allowance_kind.value,
                subscription_id=subscription_id,
                granted_count=allowance,
                expires_at=expires_at,
            )
            session.add(balance)
            await session.flush()
        else:
            balance.granted_count += allowance
        await self._repository.append_ledger_entry(
            session,
            user_id=order.telegram_user_id,
            balance_id=balance.id,
            allowance_kind=allowance_kind,
            entry_kind=LedgerEntryKind.GRANT,
            delta_count=allowance,
            idempotency_key=f"payment:{order.id}:grant",
            order_id=order.id,
            occurred_at=now,
        )
        if product_kind is not ProductKind.INTRO:
            bonus = await self._repository.balance(
                session,
                user_id=order.telegram_user_id,
                allowance_kind=AllowanceKind.BONUS,
            )
            if bonus is None:
                bonus = AllowanceBalance(
                    telegram_user_id=order.telegram_user_id,
                    allowance_kind=AllowanceKind.BONUS.value,
                    granted_count=2,
                )
                session.add(bonus)
                await session.flush()
                await self._repository.append_ledger_entry(
                    session,
                    user_id=order.telegram_user_id,
                    balance_id=bonus.id,
                    allowance_kind=AllowanceKind.BONUS,
                    entry_kind=LedgerEntryKind.GRANT,
                    delta_count=2,
                    idempotency_key=f"payment:{order.id}:first-paid-bonus",
                    order_id=order.id,
                    occurred_at=now,
                )

    async def _renew_subscription(
        self,
        session: AsyncSession,
        order: BillingOrder,
        product: Any,
        now: datetime,
    ) -> None:
        """Retire the old monthly quota and grant exactly one confirmed period."""

        subscription = await session.scalar(
            select(Subscription)
            .where(Subscription.id == order.subscription_id)
            .with_for_update()
        )
        if (
            subscription is None
            or order.renewal_period_start != subscription.billing_period_start
        ):
            raise PaymentConfirmationError
        if product.allowance is None:
            raise PaymentConfirmationError
        balance = await session.scalar(
            select(AllowanceBalance)
            .where(
                AllowanceBalance.subscription_id == subscription.id,
                AllowanceBalance.allowance_kind == AllowanceKind.MONTHLY.value,
            )
            .with_for_update()
        )
        if balance is None:
            raise PaymentConfirmationError
        remaining = (
            balance.granted_count - balance.reserved_count - balance.consumed_count
        )
        if remaining:
            await self._repository.append_ledger_entry(
                session,
                user_id=subscription.telegram_user_id,
                balance_id=balance.id,
                allowance_kind=AllowanceKind.MONTHLY,
                entry_kind=LedgerEntryKind.EXPIRE,
                delta_count=-remaining,
                idempotency_key=f"renewal:{order.id}:expire",
                order_id=order.id,
                occurred_at=now,
            )
        balance.granted_count = (
            balance.reserved_count + balance.consumed_count + product.allowance
        )
        subscription.billing_period_start = now
        subscription.billing_period_end = now + timedelta(days=30)
        subscription.renewal_failure_count = 0
        subscription.renewal_attempted_at = now
        balance.expires_at = subscription.billing_period_end
        await self._repository.append_ledger_entry(
            session,
            user_id=subscription.telegram_user_id,
            balance_id=balance.id,
            allowance_kind=AllowanceKind.MONTHLY,
            entry_kind=LedgerEntryKind.GRANT,
            delta_count=product.allowance,
            idempotency_key=f"renewal:{order.id}:grant",
            order_id=order.id,
            occurred_at=now,
        )
