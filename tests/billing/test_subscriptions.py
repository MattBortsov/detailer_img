"""Pure eligibility contract for recurring charges."""

from datetime import UTC, datetime, timedelta

from car_wrap.billing.payments import PaymentService
from car_wrap.db.models import Subscription


def test_renewal_requires_persisted_consent_and_matching_disclosure() -> None:
    now = datetime.now(UTC)
    subscription = Subscription(
        telegram_user_id=101,
        product_id="plus",
        status="active",
        provider_rebill_id="rebill",
        billing_period_start=now - timedelta(days=30),
        billing_period_end=now,
        auto_renew_consent_at=now - timedelta(days=30),
        consent_amount_kopecks=49900,
        consent_period_days=30,
    )
    assert PaymentService._eligible_renewal(subscription, now)
    subscription.consent_amount_kopecks = 1
    assert not PaymentService._eligible_renewal(subscription, now)
