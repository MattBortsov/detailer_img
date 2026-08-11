"""Billing schema remains metadata-only and carries financial invariants."""

from pathlib import Path

import pytest
from sqlalchemy import JSON, LargeBinary, String, Text

from car_wrap.db.models import (
    AllowanceBalance,
    AllowanceReservation,
    BillingLedgerEntry,
    BillingOrder,
    Subscription,
    TBankPayment,
)

pytestmark = pytest.mark.postgresql


def test_billing_models_are_bounded_metadata_only() -> None:
    models = (
        BillingOrder,
        TBankPayment,
        Subscription,
        AllowanceBalance,
        AllowanceReservation,
        BillingLedgerEntry,
    )
    forbidden_names = {
        "payload",
        "body",
        "media",
        "bytes",
        "url",
        "pan",
        "token",
        "receipt",
        "raw",
    }
    for model in models:
        for column in model.__table__.columns:
            assert not isinstance(column.type, (JSON, LargeBinary, Text))
            assert not any(name in column.name for name in forbidden_names)
            if isinstance(column.type, String):
                assert column.type.length is not None


def test_billing_models_keep_distinct_balance_buckets_and_idempotency() -> None:
    balance_columns = set(AllowanceBalance.__table__.columns.keys())
    ledger_columns = set(BillingLedgerEntry.__table__.columns.keys())
    assert {
        "allowance_kind",
        "granted_count",
        "reserved_count",
        "consumed_count",
    } <= balance_columns
    assert {
        "entry_kind",
        "allowance_kind",
        "delta_count",
        "idempotency_key",
    } <= ledger_columns
    assert "expires_at" in balance_columns
    assert all(
        "idempotency" not in column.name or column.type.length is not None
        for column in BillingLedgerEntry.__table__.columns
    )
    assert {
        "uq_allowance_balances_nonmonthly_kind",
    } <= {index.name for index in AllowanceBalance.__table__.indexes}
    assert "uq_billing_orders_active_intro_number" in {
        index.name for index in BillingOrder.__table__.indexes
    }
    assert {
        "uq_billing_ledger_one_free_grant",
        "uq_billing_ledger_one_bonus_grant",
    } <= {index.name for index in BillingLedgerEntry.__table__.indexes}
    assert "intro_number" in BillingOrder.__table__.columns


def test_billing_migration_is_reversible_and_excludes_private_payment_data() -> None:
    migration = (
        Path(__file__).parents[2] / "alembic/versions/0009_billing_ledger.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "0009"' in migration
    assert 'down_revision: str | None = "0008"' in migration
    assert "def upgrade" in migration and "def downgrade" in migration
    assert not any(
        fragment in migration.lower()
        for fragment in (
            "largebinary",
            "sa.json",
            "provider_body",
            "card_pan",
            "payment_token",
            "receipt_url",
        )
    )
