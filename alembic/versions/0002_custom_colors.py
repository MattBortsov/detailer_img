"""Create private custom color lifecycle tables."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "custom_colors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), server_default="pending", nullable=False),
        sa.Column("current_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
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
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "telegram_user_id > 0", name="ck_custom_colors_telegram_user_id_positive"
        ),
        sa.CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 40",
            name="ck_custom_colors_display_name_length",
        ),
        sa.CheckConstraint(
            "status IN ('pending','needs_review','rejected',"
            "'approved','hidden','deleted')",
            name="ck_custom_colors_status_supported",
        ),
        sa.CheckConstraint(
            "current_version > 0", name="ck_custom_colors_current_version_positive"
        ),
        sa.CheckConstraint(
            "approved_at IS NULL OR status IN ('approved','hidden','deleted')",
            name="ck_custom_colors_approval_matches_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_custom_colors"),
    )
    op.create_index(
        "ix_custom_colors_public_order",
        "custom_colors",
        ["approved_at", "id"],
        postgresql_where=sa.text("status = 'approved'"),
    )
    op.create_index(
        "ix_custom_colors_owner_active",
        "custom_colors",
        ["telegram_user_id"],
        postgresql_where=sa.text("status <> 'deleted'"),
    )
    op.create_table(
        "custom_color_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("custom_color_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(96), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("retain_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version > 0", name="ck_custom_color_versions_version_positive"
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name="ck_custom_color_versions_sha256_hex"
        ),
        sa.CheckConstraint(
            "byte_size > 0", name="ck_custom_color_versions_byte_size_positive"
        ),
        sa.CheckConstraint("width > 0", name="ck_custom_color_versions_width_positive"),
        sa.CheckConstraint(
            "height > 0", name="ck_custom_color_versions_height_positive"
        ),
        sa.CheckConstraint(
            "retain_count >= 0",
            name="ck_custom_color_versions_retain_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["custom_color_id"],
            ["custom_colors.id"],
            name="fk_custom_color_versions_custom_color_id_custom_colors",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_custom_color_versions"),
        sa.UniqueConstraint(
            "custom_color_id",
            "version",
            name="uq_custom_color_versions_custom_color_id",
        ),
        sa.UniqueConstraint("object_key", name="uq_custom_color_versions_object_key"),
    )
    op.create_table(
        "moderation_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "custom_color_version_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("provider_model", sa.String(128), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("safety_confidence", sa.Integer(), nullable=False),
        sa.Column("domain_confidence", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('approved','rejected','needs_review')",
            name="ck_moderation_attempts_decision_supported",
        ),
        sa.CheckConstraint(
            "safety_confidence BETWEEN 0 AND 10000",
            name="ck_moderation_attempts_safety_confidence_range",
        ),
        sa.CheckConstraint(
            "domain_confidence BETWEEN 0 AND 10000",
            name="ck_moderation_attempts_domain_confidence_range",
        ),
        sa.ForeignKeyConstraint(
            ["custom_color_version_id"],
            ["custom_color_versions.id"],
            name="fk_moderation_attempts_version_custom_color_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_moderation_attempts"),
        sa.UniqueConstraint(
            "custom_color_version_id",
            "idempotency_key",
            name="uq_moderation_attempts_custom_color_version_id",
        ),
    )
    op.create_table(
        "admin_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("custom_color_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_telegram_user_id > 0",
            name="ck_admin_audit_events_actor_telegram_user_id_positive",
        ),
        sa.CheckConstraint(
            "action IN ('approve','reject','rename','hide','restore','delete')",
            name="ck_admin_audit_events_action_supported",
        ),
        sa.ForeignKeyConstraint(
            ["custom_color_id"],
            ["custom_colors.id"],
            name="fk_admin_audit_events_custom_color_id_custom_colors",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_admin_audit_events"),
    )


def downgrade() -> None:
    op.drop_table("admin_audit_events")
    op.drop_table("moderation_attempts")
    op.drop_table("custom_color_versions")
    op.drop_index("ix_custom_colors_owner_active", table_name="custom_colors")
    op.drop_index("ix_custom_colors_public_order", table_name="custom_colors")
    op.drop_table("custom_colors")
