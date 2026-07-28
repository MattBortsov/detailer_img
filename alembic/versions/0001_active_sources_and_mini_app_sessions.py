"""Create active source and Mini App session metadata tables."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "active_sources",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_file_id", sa.String(length=512), nullable=False),
        sa.Column(
            "telegram_file_unique_id",
            sa.String(length=256),
            nullable=False,
        ),
        sa.Column("media_kind", sa.String(length=16), nullable=False),
        sa.Column("mime_type", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.BigInteger(), nullable=False),
        sa.Column("height", sa.BigInteger(), nullable=False),
        sa.Column(
            "accepted_at",
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
            name="ck_active_sources_telegram_user_id_positive",
        ),
        sa.CheckConstraint(
            "chat_id > 0",
            name="ck_active_sources_chat_id_positive",
        ),
        sa.CheckConstraint(
            "source_message_id > 0",
            name="ck_active_sources_source_message_id_positive",
        ),
        sa.CheckConstraint(
            "char_length(telegram_file_id) > 0",
            name="ck_active_sources_telegram_file_id_nonempty",
        ),
        sa.CheckConstraint(
            "char_length(telegram_file_unique_id) > 0",
            name="ck_active_sources_telegram_file_unique_id_nonempty",
        ),
        sa.CheckConstraint(
            "media_kind IN ('photo', 'document')",
            name="ck_active_sources_media_kind_supported",
        ),
        sa.CheckConstraint(
            "mime_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="ck_active_sources_mime_type_supported",
        ),
        sa.CheckConstraint(
            "byte_size > 0",
            name="ck_active_sources_byte_size_positive",
        ),
        sa.CheckConstraint(
            "width > 0",
            name="ck_active_sources_width_positive",
        ),
        sa.CheckConstraint(
            "height > 0",
            name="ck_active_sources_height_positive",
        ),
        sa.CheckConstraint(
            "updated_at >= accepted_at",
            name="ck_active_sources_updated_after_accepted",
        ),
        sa.PrimaryKeyConstraint(
            "telegram_user_id",
            name="pk_active_sources",
        ),
    )
    op.create_table(
        "mini_app_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("init_data_sha256", sa.String(length=64), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "auth_date",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "token_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_mini_app_sessions_token_sha256_hex",
        ),
        sa.CheckConstraint(
            "init_data_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_mini_app_sessions_init_data_sha256_hex",
        ),
        sa.CheckConstraint(
            "telegram_user_id > 0",
            name="ck_mini_app_sessions_telegram_user_id_positive",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_mini_app_sessions_expires_after_created",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_mini_app_sessions_revoked_after_created",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mini_app_sessions"),
        sa.UniqueConstraint(
            "init_data_sha256",
            name="uq_mini_app_sessions_init_data_sha256",
        ),
        sa.UniqueConstraint(
            "token_sha256",
            name="uq_mini_app_sessions_token_sha256",
        ),
    )


def downgrade() -> None:
    op.drop_table("mini_app_sessions")
    op.drop_table("active_sources")
