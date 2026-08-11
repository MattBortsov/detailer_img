"""Canaries for the metadata-only money and entitlement boundary."""

from __future__ import annotations

import inspect
from pathlib import Path

from sqlalchemy import JSON, LargeBinary

from car_wrap.billing import payments, repository, subscriptions, tbank
from car_wrap.db.models import (
    AllowanceBalance,
    AllowanceReservation,
    BillingLedgerEntry,
    BillingOrder,
    Subscription,
    TBankPayment,
)

ROOT = Path(__file__).parents[2]
CANARIES = (
    "BILLING_IMAGE_BYTES_CANARY",
    "data:image/png;base64,BILLING_CANARY",
    "TBANK_PASSWORD_CANARY",
    "RAW_TBANK_PAYLOAD_CANARY",
    "4111111111111111",
)


def test_billing_tables_are_scalar_metadata_only() -> None:
    tables = (
        BillingOrder,
        TBankPayment,
        Subscription,
        AllowanceBalance,
        AllowanceReservation,
        BillingLedgerEntry,
    )
    for table in tables:
        for column in table.__table__.columns:
            assert not isinstance(column.type, (JSON, LargeBinary))
            assert "image" not in column.name.lower()
            assert "payload" not in column.name.lower()
            assert "card" not in column.name.lower()


def test_billing_sources_do_not_log_or_serialize_sensitive_provider_content() -> None:
    source = "\n".join(
        inspect.getsource(module)
        for module in (payments, repository, subscriptions, tbank)
    ).lower()
    assert "logger.exception" not in source
    assert "response.text" not in source
    assert "response.content" not in source
    assert "request.content" not in source


def test_billing_canaries_are_absent_from_durable_code_and_operations_docs() -> None:
    paths = [
        ROOT / "src/car_wrap/billing",
        ROOT / "docs/operations/tbank-monetization.md",
    ]
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for root in paths
        for path in ([root] if root.is_file() else root.rglob("*.py"))
    )
    for canary in CANARIES:
        assert canary not in text
