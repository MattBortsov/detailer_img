"""Add a revocable source for user-initiated intro recurring payments."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intro_recurring_charge_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_invoice_id", sa.Integer(), nullable=False),
        sa.Column("amount_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "telegram_user_id > 0",
            name=op.f("ck_intro_recurring_charge_sources_telegram_user_id_positive"),
        ),
        sa.CheckConstraint(
            "parent_invoice_id > 0",
            name=op.f("ck_intro_recurring_charge_sources_parent_invoice_id_positive"),
        ),
        sa.CheckConstraint(
            "amount_kopecks = 2500",
            name=op.f("ck_intro_recurring_charge_sources_amount_is_intro_price"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'cancelled')",
            name=op.f("ck_intro_recurring_charge_sources_status_supported"),
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.telegram_user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_order_id"], ["billing_orders.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_intro_recurring_charge_sources"),
        sa.UniqueConstraint(
            "source_order_id", name="uq_intro_recurring_charge_sources_source_order_id"
        ),
        sa.UniqueConstraint(
            "parent_invoice_id",
            name="uq_intro_recurring_charge_sources_parent_invoice_id",
        ),
    )
    op.create_index(
        "uq_intro_recurring_charge_sources_active_user",
        "intro_recurring_charge_sources",
        ["telegram_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_table("intro_recurring_charge_sources")
