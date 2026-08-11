"""Add metadata-only T-Bank billing and entitlement ledger tables."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_column() -> sa.Column[object]:
    return sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False)


def upgrade() -> None:
    op.create_table(
        "billing_orders",
        _id_column(),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.String(16), nullable=False),
        sa.Column("amount_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("intro_number", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(3), server_default="RUB", nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("renewal_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recurring_consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "telegram_user_id > 0", name="ck_billing_orders_telegram_user_id_positive"
        ),
        sa.CheckConstraint(
            "product_id IN ('intro_25', 'pack_5', 'pack_15', 'pack_40', "
            "'plus', 'studio')",
            name="ck_billing_orders_product_supported",
        ),
        sa.CheckConstraint(
            "amount_kopecks > 0", name="ck_billing_orders_amount_kopecks_positive"
        ),
        sa.CheckConstraint("currency = 'RUB'", name="ck_billing_orders_currency_rub"),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'cancelled', 'failed')",
            name="ck_billing_orders_status_supported",
        ),
        sa.CheckConstraint(
            "(product_id = 'intro_25' AND intro_number BETWEEN 1 AND 3) OR "
            "(product_id <> 'intro_25' AND intro_number IS NULL)",
            name="ck_billing_orders_intro_number_matches_product",
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 64",
            name="ck_billing_orders_idempotency_key_length",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.telegram_user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_billing_orders"),
        sa.UniqueConstraint(
            "telegram_user_id",
            "idempotency_key",
            name="uq_billing_orders_telegram_user_id",
        ),
        sa.UniqueConstraint(
            "subscription_id",
            "renewal_period_start",
            name="uq_billing_orders_subscription_period",
        ),
    )
    op.create_index(
        "uq_billing_orders_active_intro_number",
        "billing_orders",
        ["telegram_user_id", "intro_number"],
        unique=True,
        postgresql_where=sa.text(
            "product_id = 'intro_25' AND status IN ('pending', 'confirmed')"
        ),
    )
    op.create_table(
        "tbank_payments",
        _id_column(),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_payment_id", sa.String(128), nullable=True),
        sa.Column("provider_order_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('initializing', 'ambiguous', 'new', 'authorized', "
            "'confirmed', 'rejected', 'cancelled')",
            name="ck_tbank_payments_status_supported",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["billing_orders.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tbank_payments"),
        sa.UniqueConstraint("order_id", name="uq_tbank_payments_order_id"),
        sa.UniqueConstraint(
            "provider_payment_id", name="uq_tbank_payments_provider_payment_id"
        ),
        sa.UniqueConstraint(
            "provider_order_id", name="uq_tbank_payments_provider_order_id"
        ),
    )
    op.create_table(
        "subscriptions",
        _id_column(),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider_rebill_id", sa.String(128), nullable=True),
        sa.Column("billing_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("billing_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auto_renew_consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_amount_kopecks", sa.BigInteger(), nullable=True),
        sa.Column("consent_period_days", sa.Integer(), nullable=True),
        sa.Column(
            "renewal_failure_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("renewal_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "telegram_user_id > 0", name="ck_subscriptions_telegram_user_id_positive"
        ),
        sa.CheckConstraint(
            "product_id IN ('plus', 'studio')",
            name="ck_subscriptions_product_supported",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'cancelled', 'past_due', 'expired')",
            name="ck_subscriptions_status_supported",
        ),
        sa.CheckConstraint(
            "billing_period_end > billing_period_start",
            name="ck_subscriptions_billing_period_order",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.telegram_user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_subscriptions"),
        sa.UniqueConstraint(
            "telegram_user_id",
            "product_id",
            "billing_period_start",
            name="uq_subscriptions_telegram_user_id",
        ),
        sa.UniqueConstraint(
            "provider_rebill_id", name="uq_subscriptions_provider_rebill_id"
        ),
    )
    op.create_table(
        "allowance_balances",
        _id_column(),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("allowance_kind", sa.String(16), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("granted_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reserved_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("consumed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "telegram_user_id > 0",
            name="ck_allowance_balances_telegram_user_id_positive",
        ),
        sa.CheckConstraint(
            "allowance_kind IN ('free', 'intro', 'package', 'bonus', 'monthly')",
            name="ck_allowance_balances_allowance_kind_supported",
        ),
        sa.CheckConstraint(
            "granted_count >= 0 AND reserved_count >= 0 AND consumed_count >= 0",
            name="ck_allowance_balances_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "reserved_count + consumed_count <= granted_count",
            name="ck_allowance_balances_counts_within_grant",
        ),
        sa.CheckConstraint(
            "(allowance_kind = 'monthly') = (subscription_id IS NOT NULL)",
            name="ck_allowance_balances_monthly_requires_subscription",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR allowance_kind = 'monthly'",
            name="ck_allowance_balances_expiry_monthly_only",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.telegram_user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["subscriptions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_allowance_balances"),
        sa.UniqueConstraint(
            "telegram_user_id",
            "allowance_kind",
            "subscription_id",
            name="uq_allowance_balances_telegram_user_id",
        ),
    )
    op.create_foreign_key(
        "fk_billing_orders_subscription",
        "billing_orders",
        "subscriptions",
        ["subscription_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "ultima_leads",
        _id_column(),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "telegram_user_id > 0", name="ck_ultima_leads_telegram_user_id_positive"
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.telegram_user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ultima_leads"),
        sa.UniqueConstraint(
            "telegram_user_id", "created_at", name="uq_ultima_leads_telegram_user_id"
        ),
    )
    op.create_index(
        "uq_allowance_balances_nonmonthly_kind",
        "allowance_balances",
        ["telegram_user_id", "allowance_kind"],
        unique=True,
        postgresql_where=sa.text("subscription_id IS NULL"),
    )
    op.create_table(
        "allowance_reservations",
        _id_column(),
        sa.Column("balance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(16), server_default="reserved", nullable=False),
        sa.Column(
            "reserved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "quantity = 1", name="ck_allowance_reservations_single_generation_only"
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'consumed', 'released')",
            name="ck_allowance_reservations_status_supported",
        ),
        sa.ForeignKeyConstraint(
            ["balance_id"], ["allowance_balances.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["generation_jobs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_allowance_reservations"),
        sa.UniqueConstraint("job_id", name="uq_allowance_reservations_job_id"),
    )
    op.create_table(
        "billing_ledger_entries",
        _id_column(),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("balance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("allowance_kind", sa.String(16), nullable=False),
        sa.Column("entry_kind", sa.String(16), nullable=False),
        sa.Column("delta_count", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "telegram_user_id > 0",
            name="ck_billing_ledger_entries_telegram_user_id_positive",
        ),
        sa.CheckConstraint(
            "allowance_kind IN ('free', 'intro', 'package', 'bonus', 'monthly')",
            name="ck_billing_ledger_entries_allowance_kind_supported",
        ),
        sa.CheckConstraint(
            "entry_kind IN ('grant', 'reserve', 'consume', 'release', 'expire')",
            name="ck_billing_ledger_entries_entry_kind_supported",
        ),
        sa.CheckConstraint(
            "delta_count <> 0", name="ck_billing_ledger_entries_delta_nonzero"
        ),
        sa.CheckConstraint(
            "(allowance_kind <> 'free' OR entry_kind <> 'grant' OR "
            "delta_count = 1) AND (allowance_kind <> 'bonus' OR "
            "entry_kind <> 'grant' OR delta_count = 2)",
            name="ck_billing_ledger_entries_free_and_bonus_grant_sizes",
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 64",
            name="ck_billing_ledger_entries_idempotency_key_length",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.telegram_user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["balance_id"], ["allowance_balances.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["billing_orders.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"], ["allowance_reservations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["generation_jobs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_billing_ledger_entries"),
        sa.UniqueConstraint(
            "telegram_user_id",
            "idempotency_key",
            name="uq_billing_ledger_entries_telegram_user_id",
        ),
    )
    op.create_index(
        "uq_billing_ledger_one_free_grant",
        "billing_ledger_entries",
        ["telegram_user_id"],
        unique=True,
        postgresql_where=sa.text("allowance_kind = 'free' AND entry_kind = 'grant'"),
    )
    op.create_index(
        "uq_billing_ledger_one_bonus_grant",
        "billing_ledger_entries",
        ["telegram_user_id"],
        unique=True,
        postgresql_where=sa.text("allowance_kind = 'bonus' AND entry_kind = 'grant'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_billing_ledger_one_bonus_grant", table_name="billing_ledger_entries"
    )
    op.drop_index(
        "uq_billing_ledger_one_free_grant", table_name="billing_ledger_entries"
    )
    op.drop_table("billing_ledger_entries")
    op.drop_table("allowance_reservations")
    op.drop_index(
        "uq_allowance_balances_nonmonthly_kind", table_name="allowance_balances"
    )
    op.drop_table("allowance_balances")
    op.drop_table("ultima_leads")
    op.drop_table("tbank_payments")
    op.drop_table("billing_orders")
    op.drop_table("subscriptions")
