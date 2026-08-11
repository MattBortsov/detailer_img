"""Stored-order checkout and transactional Robokassa confirmation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from car_wrap.billing.catalog import get_payable_product
from car_wrap.billing.contracts import (
    AllowanceKind,
    LedgerEntryKind,
    Product,
    ProductKind,
)
from car_wrap.billing.gateway import (
    PaymentGatewayClient,
    PaymentGatewayOutcomeAmbiguous,
    PaymentGatewayProtocolError,
    PaymentGatewayRequestNotSent,
)
from car_wrap.billing.repository import BillingRepository
from car_wrap.db.models import (
    AllowanceBalance,
    BillingOrder,
    IntroRecurringChargeSource,
    RobokassaPayment,
    Subscription,
)


class PaymentConfirmationError(ValueError):
    """A gateway callback did not match a stored commercial intent."""


class PaymentService:
    """Keep provider metadata bounded and grant only inside one transaction."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        gateway: PaymentGatewayClient,
        repository: BillingRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._gateway = gateway
        self._repository = repository or BillingRepository()

    async def start_checkout(
        self,
        *,
        user_id: int,
        product_id: str,
        idempotency_key: str,
        recurring_consent_at: datetime | None = None,
    ) -> tuple[BillingOrder, str]:
        """Persist a catalog-priced order and return its signed checkout URL."""

        self._gateway.ensure_available()
        product = get_payable_product(product_id)
        amount_kopecks = product.amount_kopecks
        if amount_kopecks is None:
            raise PaymentConfirmationError
        if product.kind is ProductKind.MONTHLY and recurring_consent_at is None:
            raise PaymentConfirmationError("recurring consent is required")

        async with self._session_factory() as session:
            async with session.begin():
                await self._repository.lock_account(session, user_id=user_id)
                intro_number = await self._next_intro_number(
                    session,
                    user_id=user_id,
                    product_kind=product.kind,
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
                session.add(
                    RobokassaPayment(
                        order_id=order.id,
                        invoice_id=None,
                        previous_invoice_id=None,
                        status="initializing",
                    )
                )

        try:
            checkout = await self._gateway.create_checkout(
                external_order_id=str(order.id),
                amount_kopecks=amount_kopecks,
                description=f"Car Wrap: {product.id.value}",
                recurring=product.kind in {ProductKind.INTRO, ProductKind.MONTHLY},
            )
        except PaymentGatewayOutcomeAmbiguous:
            raise
        except (PaymentGatewayRequestNotSent, PaymentGatewayProtocolError):
            await self._mark_gateway_rejected(order_id=order.id)
            raise
        await self._record_gateway_invoice(
            order_id=order.id,
            invoice_id=checkout.invoice_id,
            status="pending",
        )
        return order, checkout.redirect_url

    async def has_active_intro_recurring_source(self, *, user_id: int) -> bool:
        async with self._session_factory() as session:
            source = await session.scalar(
                select(IntroRecurringChargeSource.id)
                .where(
                    IntroRecurringChargeSource.telegram_user_id == user_id,
                    IntroRecurringChargeSource.status == "active",
                )
                .limit(1)
            )
        return source is not None

    async def start_intro_recurring_charge(
        self, *, user_id: int
    ) -> BillingOrder | None:
        """Create one user-triggered 25 ₽ charge without opening a payment page."""

        self._gateway.ensure_available()
        product = get_payable_product("intro_25")
        async with self._session_factory() as session:
            async with session.begin():
                await self._repository.lock_account(session, user_id=user_id)
                source = await session.scalar(
                    select(IntroRecurringChargeSource)
                    .where(
                        IntroRecurringChargeSource.telegram_user_id == user_id,
                        IntroRecurringChargeSource.status == "active",
                    )
                    .with_for_update()
                )
                if source is None:
                    return None
                parent_invoice_id = source.parent_invoice_id
                pending_charge = await session.scalar(
                    select(BillingOrder.id)
                    .where(
                        BillingOrder.telegram_user_id == user_id,
                        BillingOrder.product_id == product.id.value,
                        BillingOrder.status == "pending",
                    )
                    .limit(1)
                )
                if pending_charge is not None:
                    return None
                try:
                    intro_number = await self._next_intro_number(
                        session,
                        user_id=user_id,
                        product_kind=product.kind,
                    )
                except PaymentConfirmationError:
                    return None
                order = BillingOrder(
                    telegram_user_id=user_id,
                    product_id=product.id.value,
                    amount_kopecks=product.amount_kopecks or 0,
                    intro_number=intro_number,
                    idempotency_key=f"intro-recurring:{uuid4().hex}",
                )
                session.add(order)
                await session.flush()
                session.add(
                    RobokassaPayment(
                        order_id=order.id,
                        invoice_id=None,
                        previous_invoice_id=parent_invoice_id,
                        status="initializing",
                    )
                )

        try:
            recurring = await self._gateway.submit_recurring(
                external_order_id=str(order.id),
                previous_invoice_id=parent_invoice_id,
                amount_kopecks=product.amount_kopecks or 0,
                description="Car Wrap: intro_25",
            )
        except PaymentGatewayOutcomeAmbiguous:
            raise
        except (PaymentGatewayRequestNotSent, PaymentGatewayProtocolError):
            await self._mark_gateway_rejected(order_id=order.id)
            raise
        await self._record_gateway_invoice(
            order_id=order.id,
            invoice_id=recurring.invoice_id,
            status="submitted",
        )
        return order

    async def cancel_intro_recurring_source(self, *, user_id: int) -> bool:
        """Prevent future server-initiated 25 ₽ charges for one user."""

        async with self._session_factory() as session:
            async with session.begin():
                await self._repository.lock_account(session, user_id=user_id)
                source = await session.scalar(
                    select(IntroRecurringChargeSource)
                    .where(
                        IntroRecurringChargeSource.telegram_user_id == user_id,
                        IntroRecurringChargeSource.status == "active",
                    )
                    .with_for_update()
                )
                if source is None:
                    return False
                source.status = "cancelled"
                source.cancelled_at = datetime.now(UTC)
        return True

    async def start_renewal(
        self,
        *,
        subscription_id: UUID,
        now: datetime,
    ) -> BillingOrder | None:
        """Create one child invoice and ask Robokassa to start its operation.

        The accepted recurring response is never treated as payment success.
        Only a later signed ResultURL can grant the next period.
        """

        self._gateway.ensure_available()
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
                    return None

                product = get_payable_product(subscription.product_id)
                amount_kopecks = product.amount_kopecks
                parent_invoice_id = subscription.robokassa_parent_invoice_id
                if amount_kopecks is None or parent_invoice_id is None:
                    return None
                order = BillingOrder(
                    telegram_user_id=subscription.telegram_user_id,
                    product_id=product.id.value,
                    amount_kopecks=amount_kopecks,
                    idempotency_key=f"renewal:{uuid4().hex}",
                    subscription_id=subscription.id,
                    renewal_period_start=subscription.billing_period_start,
                )
                subscription.renewal_attempted_at = now
                session.add(order)
                await session.flush()
                session.add(
                    RobokassaPayment(
                        order_id=order.id,
                        invoice_id=None,
                        previous_invoice_id=parent_invoice_id,
                        status="initializing",
                    )
                )

        try:
            recurring = await self._gateway.submit_recurring(
                external_order_id=str(order.id),
                previous_invoice_id=parent_invoice_id,
                amount_kopecks=amount_kopecks,
                description=f"Car Wrap renewal: {order.product_id}",
            )
        except PaymentGatewayOutcomeAmbiguous:
            raise
        except (PaymentGatewayRequestNotSent, PaymentGatewayProtocolError):
            await self._mark_renewal_rejected(
                order_id=order.id,
                subscription_id=subscription_id,
                now=now,
            )
            raise
        await self._record_gateway_invoice(
            order_id=order.id,
            invoice_id=recurring.invoice_id,
            status="submitted",
        )
        return order

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

    async def _payment_for_order(
        self,
        session: AsyncSession,
        *,
        order_id: UUID,
    ) -> RobokassaPayment | None:
        payment = await session.scalar(
            select(RobokassaPayment)
            .where(RobokassaPayment.order_id == order_id)
            .with_for_update()
        )
        return payment if isinstance(payment, RobokassaPayment) else None

    async def _record_gateway_invoice(
        self,
        *,
        order_id: UUID,
        invoice_id: int,
        status: str,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                payment = await self._payment_for_order(
                    session,
                    order_id=order_id,
                )
                if payment is None:
                    raise PaymentConfirmationError("gateway order is unavailable")
                if payment.invoice_id not in {None, invoice_id}:
                    raise PaymentConfirmationError("gateway invoice mismatch")
                # A recurring ResultURL can arrive before the gateway HTTP
                # response. Confirmation already persisted the same invoice,
                # so this late response must not turn success into an error.
                if payment.status == "confirmed":
                    return
                if payment.status != "initializing":
                    raise PaymentConfirmationError(
                        "gateway order is no longer initializing"
                    )
                payment.invoice_id = invoice_id
                payment.status = status

    async def _mark_gateway_rejected(self, *, order_id: UUID) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                payment = await self._payment_for_order(session, order_id=order_id)
                if payment is None or payment.status == "confirmed":
                    return
                payment.status = "rejected"
                order = await session.get(
                    BillingOrder,
                    order_id,
                    with_for_update=True,
                )
                if order is not None and order.status == "pending":
                    order.status = "failed"

    async def _mark_renewal_rejected(
        self,
        *,
        order_id: UUID,
        subscription_id: UUID,
        now: datetime,
    ) -> None:
        """Record a definitive failure without touching package allowances."""

        async with self._session_factory() as session:
            async with session.begin():
                payment = await self._payment_for_order(session, order_id=order_id)
                if payment is None or payment.status == "confirmed":
                    return
                payment.status = "rejected"
                order = await session.get(
                    BillingOrder,
                    payment.order_id,
                    with_for_update=True,
                )
                if order is not None and order.status == "pending":
                    order.status = "failed"
                subscription = await session.scalar(
                    select(Subscription)
                    .where(Subscription.id == subscription_id)
                    .with_for_update()
                )
                if subscription is None or subscription.status == "cancelled":
                    return
                subscription.renewal_failure_count += 1
                subscription.renewal_attempted_at = now
                subscription.status = "past_due"

    @staticmethod
    def _eligible_renewal(subscription: Subscription | None, now: datetime) -> bool:
        if subscription is None:
            return False
        product = get_payable_product(subscription.product_id)
        return bool(
            subscription.status == "active"
            and subscription.cancelled_at is None
            and subscription.robokassa_parent_invoice_id
            and subscription.auto_renew_consent_at
            and subscription.consent_amount_kopecks == product.amount_kopecks
            and subscription.consent_period_days == 30
            and subscription.billing_period_end <= now
            and (subscription.renewal_failure_count or 0) < 3
        )

    def production_available(self) -> bool:
        """Expose only the fail-closed movement predicate to transport adapters."""

        return self._gateway.production_available

    def verify_gateway_callback(
        self,
        *,
        timestamp: str | None,
        signature: str | None,
        body: bytes,
    ) -> bool:
        return self._gateway.verify_callback(
            timestamp=timestamp,
            signature=signature,
            body=body,
        )

    async def confirm_result(
        self,
        *,
        external_order_id: str,
        invoice_id: int,
        amount_kopecks: int,
    ) -> int | None:
        """Confirm once and return the credited Telegram user for notification."""

        try:
            order_id = UUID(external_order_id)
        except ValueError as exc:
            raise PaymentConfirmationError("invalid gateway order ID") from exc
        if invoice_id <= 0 or amount_kopecks <= 0:
            raise PaymentConfirmationError("invalid gateway payment")

        async with self._session_factory() as session:
            async with session.begin():
                payment = await self._payment_for_order(session, order_id=order_id)
                if payment is None:
                    raise PaymentConfirmationError("unknown gateway order")
                order = await session.scalar(
                    select(BillingOrder)
                    .where(BillingOrder.id == order_id)
                    .with_for_update()
                )
                if order is None:
                    raise PaymentConfirmationError("missing persisted order")
                if payment.invoice_id not in {None, invoice_id}:
                    raise PaymentConfirmationError("gateway invoice mismatch")
                if amount_kopecks != order.amount_kopecks or order.currency != "RUB":
                    raise PaymentConfirmationError("gateway amount mismatch")
                product = get_payable_product(order.product_id)
                if product.amount_kopecks != order.amount_kopecks:
                    raise PaymentConfirmationError("catalog amount mismatch")
                if payment.status == "confirmed" and order.status == "confirmed":
                    return None
                if payment.status not in {"initializing", "pending", "submitted"}:
                    raise PaymentConfirmationError("invoice is not confirmable")
                if order.status != "pending":
                    raise PaymentConfirmationError("order is not pending")

                await self._repository.lock_account(
                    session,
                    user_id=order.telegram_user_id,
                )
                confirmed_at = datetime.now(UTC)
                payment.invoice_id = invoice_id
                payment.status = "confirmed"
                payment.confirmed_at = confirmed_at
                order.status = "confirmed"
                order.confirmed_at = confirmed_at
                if order.subscription_id is not None:
                    await self._renew_subscription(
                        session,
                        order,
                        payment,
                        product,
                        confirmed_at,
                    )
                else:
                    await self._grant_purchase(
                        session,
                        order,
                        payment,
                        product,
                        confirmed_at,
                    )
                credited_user_id = order.telegram_user_id
        return credited_user_id

    async def _grant_purchase(
        self,
        session: AsyncSession,
        order: BillingOrder,
        payment: RobokassaPayment,
        product: Product,
        now: datetime,
    ) -> None:
        allowance = product.allowance
        if allowance is None:
            raise PaymentConfirmationError
        subscription_id: UUID | None = None
        expires_at: datetime | None = None
        allowance_kind = (
            AllowanceKind.INTRO
            if product.kind is ProductKind.INTRO
            else AllowanceKind.PACKAGE
        )
        if product.kind is ProductKind.MONTHLY:
            if order.recurring_consent_at is None or payment.invoice_id is None:
                raise PaymentConfirmationError
            subscription = Subscription(
                telegram_user_id=order.telegram_user_id,
                product_id=order.product_id,
                status="active",
                robokassa_parent_invoice_id=payment.invoice_id,
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
        elif product.kind is ProductKind.INTRO:
            if payment.invoice_id is None:
                raise PaymentConfirmationError
            if payment.previous_invoice_id is None:
                active_source = await session.scalar(
                    select(IntroRecurringChargeSource.id)
                    .where(
                        IntroRecurringChargeSource.telegram_user_id
                        == order.telegram_user_id,
                        IntroRecurringChargeSource.status == "active",
                    )
                    .limit(1)
                )
                if active_source is None:
                    session.add(
                        IntroRecurringChargeSource(
                            telegram_user_id=order.telegram_user_id,
                            source_order_id=order.id,
                            parent_invoice_id=payment.invoice_id,
                            amount_kopecks=order.amount_kopecks,
                        )
                    )
            else:
                source = await session.scalar(
                    select(IntroRecurringChargeSource.id)
                    .where(
                        IntroRecurringChargeSource.telegram_user_id
                        == order.telegram_user_id,
                        IntroRecurringChargeSource.parent_invoice_id
                        == payment.previous_invoice_id,
                        IntroRecurringChargeSource.status == "active",
                    )
                    .limit(1)
                )
                if source is None:
                    raise PaymentConfirmationError
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
        if product.kind is not ProductKind.INTRO:
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
        payment: RobokassaPayment,
        product: Product,
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
            or payment.previous_invoice_id != subscription.robokassa_parent_invoice_id
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
