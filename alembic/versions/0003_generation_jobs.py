"""Create durable generation job and dispatch-intent tables."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generation_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "client_submission_uuid",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_file_id", sa.String(512), nullable=False),
        sa.Column("telegram_file_unique_id", sa.String(256), nullable=False),
        sa.Column("source_media_kind", sa.String(16), nullable=False),
        sa.Column("source_mime_type", sa.String(64), nullable=False),
        sa.Column("source_byte_size", sa.BigInteger(), nullable=False),
        sa.Column("source_width", sa.Integer(), nullable=False),
        sa.Column("source_height", sa.Integer(), nullable=False),
        sa.Column("intent_kind", sa.String(16), nullable=False),
        sa.Column("palette_color_id", sa.String(64), nullable=True),
        sa.Column(
            "custom_color_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("custom_color_sha256", sa.String(64), nullable=True),
        sa.Column("intent_display_name", sa.String(40), nullable=False),
        sa.Column("image_model", sa.String(128), nullable=False),
        sa.Column("prompt_revision", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_summary", sa.String(240), nullable=True),
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
            name="ck_generation_jobs_telegram_user_id_positive",
        ),
        sa.CheckConstraint(
            "chat_id > 0",
            name="ck_generation_jobs_chat_id_positive",
        ),
        sa.CheckConstraint(
            "source_message_id > 0",
            name="ck_generation_jobs_source_message_id_positive",
        ),
        sa.CheckConstraint(
            "char_length(telegram_file_id) > 0",
            name="ck_generation_jobs_telegram_file_id_nonempty",
        ),
        sa.CheckConstraint(
            "char_length(telegram_file_unique_id) > 0",
            name="ck_generation_jobs_telegram_file_unique_id_nonempty",
        ),
        sa.CheckConstraint(
            "source_media_kind IN ('photo', 'document')",
            name="ck_generation_jobs_source_media_kind_supported",
        ),
        sa.CheckConstraint(
            "source_mime_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="ck_generation_jobs_source_mime_type_supported",
        ),
        sa.CheckConstraint(
            "source_byte_size > 0",
            name="ck_generation_jobs_source_byte_size_positive",
        ),
        sa.CheckConstraint(
            "source_width > 0",
            name="ck_generation_jobs_source_width_positive",
        ),
        sa.CheckConstraint(
            "source_height > 0",
            name="ck_generation_jobs_source_height_positive",
        ),
        sa.CheckConstraint(
            "intent_kind IN ('palette', 'custom', 'surprise')",
            name="ck_generation_jobs_intent_kind_supported",
        ),
        sa.CheckConstraint(
            "("
            "intent_kind = 'palette' "
            "AND palette_color_id IS NOT NULL "
            "AND custom_color_version_id IS NULL "
            "AND custom_color_sha256 IS NULL"
            ") OR ("
            "intent_kind = 'custom' "
            "AND palette_color_id IS NULL "
            "AND custom_color_version_id IS NOT NULL "
            "AND custom_color_sha256 IS NOT NULL"
            ") OR ("
            "intent_kind = 'surprise' "
            "AND palette_color_id IS NULL "
            "AND custom_color_version_id IS NULL "
            "AND custom_color_sha256 IS NULL"
            ")",
            name="ck_generation_jobs_intent_shape",
        ),
        sa.CheckConstraint(
            "custom_color_sha256 IS NULL OR custom_color_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_generation_jobs_custom_color_sha256_hex",
        ),
        sa.CheckConstraint(
            "char_length(intent_display_name) BETWEEN 1 AND 40",
            name="ck_generation_jobs_intent_display_name_length",
        ),
        sa.CheckConstraint(
            "char_length(image_model) BETWEEN 1 AND 128",
            name="ck_generation_jobs_image_model_length",
        ),
        sa.CheckConstraint(
            "prompt_revision ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'",
            name="ck_generation_jobs_prompt_revision_safe",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_generation_jobs_status_supported",
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND error_code IS NOT NULL) "
            "OR (status <> 'failed' "
            "AND error_code IS NULL AND error_summary IS NULL)",
            name="ck_generation_jobs_error_matches_status",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_generation_jobs_error_code_safe",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_generation_jobs_updated_after_created",
        ),
        sa.ForeignKeyConstraint(
            ["custom_color_version_id"],
            ["custom_color_versions.id"],
            name="fk_generation_jobs_custom_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_generation_jobs"),
        sa.UniqueConstraint(
            "telegram_user_id",
            "client_submission_uuid",
            name="uq_generation_jobs_telegram_user_id",
        ),
    )
    op.create_index(
        "ix_generation_jobs_user_active",
        "generation_jobs",
        ["telegram_user_id"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_generation_jobs_user_created",
        "generation_jobs",
        ["telegram_user_id", "created_at"],
    )
    op.create_index(
        "ix_generation_jobs_status_created",
        "generation_jobs",
        ["status", "created_at"],
    )
    op.create_table(
        "job_outbox",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "publish_attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "publish_attempts >= 0",
            name="ck_job_outbox_publish_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "(publish_attempts = 0 AND last_attempt_at IS NULL) "
            "OR (publish_attempts > 0 AND last_attempt_at IS NOT NULL)",
            name="ck_job_outbox_attempt_time_consistent",
        ),
        sa.CheckConstraint(
            "published_at IS NULL OR publish_attempts > 0",
            name="ck_job_outbox_published_has_attempt",
        ),
        sa.CheckConstraint(
            "published_at IS NULL OR published_at >= created_at",
            name="ck_job_outbox_published_after_created",
        ),
        sa.CheckConstraint(
            "last_attempt_at IS NULL OR last_attempt_at >= created_at",
            name="ck_job_outbox_attempt_after_created",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["generation_jobs.id"],
            name="fk_job_outbox_job_id_generation_jobs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id", name="pk_job_outbox"),
    )
    op.create_index(
        "ix_job_outbox_pending",
        "job_outbox",
        ["created_at", "job_id"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_job_outbox_pending", table_name="job_outbox")
    op.drop_table("job_outbox")
    op.drop_index("ix_generation_jobs_status_created", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_user_created", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_user_active", table_name="generation_jobs")
    op.drop_table("generation_jobs")
