"""Replace inactive T-Bank metadata with Robokassa invoice correlation."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_column() -> sa.Column[object]:
    return sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False)


def upgrade() -> None:
    # The owner confirmed there are no active T-Bank subscriptions to preserve.
    op.drop_table("tbank_payments")
    op.drop_constraint(
        "uq_subscriptions_provider_rebill_id",
        "subscriptions",
        type_="unique",
    )
    op.drop_column("subscriptions", "provider_rebill_id")

    op.add_column(
        "subscriptions",
        sa.Column("robokassa_parent_invoice_id", sa.Integer(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_subscriptions_robokassa_parent_invoice_id",
        "subscriptions",
        ["robokassa_parent_invoice_id"],
    )
    op.create_check_constraint(
        op.f("ck_subscriptions_robokassa_parent_invoice_id_positive"),
        "subscriptions",
        "robokassa_parent_invoice_id IS NULL "
        "OR robokassa_parent_invoice_id > 0",
    )

    op.create_table(
        "robokassa_payments",
        _id_column(),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("previous_invoice_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "invoice_id IS NULL OR invoice_id > 0",
            name=op.f("ck_robokassa_payments_invoice_id_positive"),
        ),
        sa.CheckConstraint(
            "previous_invoice_id IS NULL OR previous_invoice_id > 0",
            name=op.f("ck_robokassa_payments_previous_invoice_id_positive"),
        ),
        sa.CheckConstraint(
            "previous_invoice_id IS NULL OR previous_invoice_id <> invoice_id",
            name=op.f("ck_robokassa_payments_parent_invoice_differs"),
        ),
        sa.CheckConstraint(
            "status IN ('initializing', 'pending', 'submitted', "
            "'confirmed', 'rejected')",
            name=op.f("ck_robokassa_payments_status_supported"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["billing_orders.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_robokassa_payments"),
        sa.UniqueConstraint("order_id", name="uq_robokassa_payments_order_id"),
        sa.UniqueConstraint(
            "invoice_id",
            name="uq_robokassa_payments_invoice_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("robokassa_payments")
    op.drop_constraint(
        op.f("ck_subscriptions_robokassa_parent_invoice_id_positive"),
        "subscriptions",
        type_="check",
    )
    op.drop_constraint(
        "uq_subscriptions_robokassa_parent_invoice_id",
        "subscriptions",
        type_="unique",
    )
    op.drop_column("subscriptions", "robokassa_parent_invoice_id")
    op.add_column(
        "subscriptions",
        sa.Column("provider_rebill_id", sa.String(128), nullable=True),
    )
    op.create_unique_constraint(
        "uq_subscriptions_provider_rebill_id",
        "subscriptions",
        ["provider_rebill_id"],
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
            ["order_id"],
            ["billing_orders.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tbank_payments"),
        sa.UniqueConstraint("order_id", name="uq_tbank_payments_order_id"),
        sa.UniqueConstraint(
            "provider_payment_id",
            name="uq_tbank_payments_provider_payment_id",
        ),
        sa.UniqueConstraint(
            "provider_order_id",
            name="uq_tbank_payments_provider_order_id",
        ),
    )
