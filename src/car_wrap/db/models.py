"""Metadata-only ownership and Mini App session models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from car_wrap.db.base import Base


class ActiveSource(Base):
    """The newest validated Telegram image reference for one user."""

    __tablename__ = "active_sources"
    __table_args__ = (
        CheckConstraint(
            "telegram_user_id > 0",
            name="telegram_user_id_positive",
        ),
        CheckConstraint("chat_id > 0", name="chat_id_positive"),
        CheckConstraint(
            "source_message_id > 0",
            name="source_message_id_positive",
        ),
        CheckConstraint(
            "char_length(telegram_file_id) > 0",
            name="telegram_file_id_nonempty",
        ),
        CheckConstraint(
            "char_length(telegram_file_unique_id) > 0",
            name="telegram_file_unique_id_nonempty",
        ),
        CheckConstraint(
            "media_kind IN ('photo', 'document')",
            name="media_kind_supported",
        ),
        CheckConstraint(
            "mime_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="mime_type_supported",
        ),
        CheckConstraint("byte_size > 0", name="byte_size_positive"),
        CheckConstraint("width > 0", name="width_positive"),
        CheckConstraint("height > 0", name="height_positive"),
        CheckConstraint(
            "updated_at >= accepted_at",
            name="updated_after_accepted",
        ),
    )

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    telegram_file_unique_id: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
    )
    media_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int] = mapped_column(BigInteger, nullable=False)
    height: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class MiniAppSession(Base):
    """One-time authenticated Telegram launch and its opaque session."""

    __tablename__ = "mini_app_sessions"
    __table_args__ = (
        UniqueConstraint("token_sha256"),
        UniqueConstraint("init_data_sha256"),
        CheckConstraint(
            "token_sha256 ~ '^[0-9a-f]{64}$'",
            name="token_sha256_hex",
        ),
        CheckConstraint(
            "init_data_sha256 ~ '^[0-9a-f]{64}$'",
            name="init_data_sha256_hex",
        ),
        CheckConstraint(
            "telegram_user_id > 0",
            name="telegram_user_id_positive",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="expires_after_created",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="revoked_after_created",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    init_data_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    auth_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class CustomColor(Base):
    """Owner-managed custom wrap color and its publication lifecycle."""

    __tablename__ = "custom_colors"
    __table_args__ = (
        CheckConstraint(
            "telegram_user_id > 0",
            name="telegram_user_id_positive",
        ),
        CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 40",
            name="display_name_length",
        ),
        CheckConstraint(
            "status IN ('pending', 'needs_review', 'rejected', "
            "'approved', 'hidden', 'deleted')",
            name="status_supported",
        ),
        CheckConstraint(
            "current_version > 0",
            name="current_version_positive",
        ),
        CheckConstraint(
            "approved_at IS NULL OR status IN ('approved', 'hidden', 'deleted')",
            name="approval_matches_status",
        ),
        Index(
            "ix_custom_colors_public_order",
            "approved_at",
            "id",
            postgresql_where=text("status = 'approved'"),
        ),
        Index(
            "ix_custom_colors_owner_active",
            "telegram_user_id",
            postgresql_where=text("status <> 'deleted'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    display_name: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    current_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class CustomColorVersion(Base):
    """Immutable canonical reference bytes for one color version."""

    __tablename__ = "custom_color_versions"
    __table_args__ = (
        UniqueConstraint("custom_color_id", "version"),
        UniqueConstraint("object_key"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="sha256_hex",
        ),
        CheckConstraint("byte_size > 0", name="byte_size_positive"),
        CheckConstraint("width > 0", name="width_positive"),
        CheckConstraint("height > 0", name="height_positive"),
        CheckConstraint("retain_count >= 0", name="retain_count_nonnegative"),
        CheckConstraint(
            "color_structure IN ('unspecified', 'solid', 'multicolor')",
            name="color_structure_supported",
        ),
        CheckConstraint(
            "finish IN ('unspecified', 'matte', 'satin', 'gloss')",
            name="finish_supported",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    custom_color_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("custom_colors.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    object_key: Mapped[str] = mapped_column(String(96), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    color_structure: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="unspecified",
        server_default="unspecified",
    )
    finish: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="unspecified",
        server_default="unspecified",
    )
    analysis_revision: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    color_profile: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    retain_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class GenerationJob(Base):
    """Immutable metadata snapshot for one accepted generation request."""

    __tablename__ = "generation_jobs"
    __table_args__ = (
        UniqueConstraint("telegram_user_id", "client_submission_uuid"),
        CheckConstraint(
            "telegram_user_id > 0",
            name="telegram_user_id_positive",
        ),
        CheckConstraint("chat_id > 0", name="chat_id_positive"),
        CheckConstraint(
            "source_message_id > 0",
            name="source_message_id_positive",
        ),
        CheckConstraint(
            "char_length(telegram_file_id) > 0",
            name="telegram_file_id_nonempty",
        ),
        CheckConstraint(
            "char_length(telegram_file_unique_id) > 0",
            name="telegram_file_unique_id_nonempty",
        ),
        CheckConstraint(
            "source_media_kind IN ('photo', 'document')",
            name="source_media_kind_supported",
        ),
        CheckConstraint(
            "source_mime_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="source_mime_type_supported",
        ),
        CheckConstraint(
            "source_byte_size > 0",
            name="source_byte_size_positive",
        ),
        CheckConstraint("source_width > 0", name="source_width_positive"),
        CheckConstraint("source_height > 0", name="source_height_positive"),
        CheckConstraint(
            "intent_kind IN ('palette', 'custom', 'surprise')",
            name="intent_kind_supported",
        ),
        CheckConstraint(
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
            name="intent_shape",
        ),
        CheckConstraint(
            "custom_color_sha256 IS NULL OR custom_color_sha256 ~ '^[0-9a-f]{64}$'",
            name="custom_color_sha256_hex",
        ),
        CheckConstraint(
            "char_length(intent_display_name) BETWEEN 1 AND 40",
            name="intent_display_name_length",
        ),
        CheckConstraint(
            "char_length(image_model) BETWEEN 1 AND 128",
            name="image_model_length",
        ),
        CheckConstraint(
            "prompt_revision ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'",
            name="prompt_revision_safe",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="status_supported",
        ),
        CheckConstraint(
            "(status = 'failed' AND error_code IS NOT NULL) "
            "OR (status <> 'failed' "
            "AND error_code IS NULL AND error_summary IS NULL)",
            name="error_matches_status",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="error_code_safe",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="updated_after_created",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL) "
            "OR (status <> 'running' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL AND heartbeat_at IS NULL)",
            name="lease_matches_running",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed') AND terminal_at IS NOT NULL) "
            "OR (status IN ('queued', 'running') AND terminal_at IS NULL)",
            name="terminal_time_matches_status",
        ),
        CheckConstraint(
            "(status = 'succeeded' AND result_message_id > 0) "
            "OR (status <> 'succeeded' AND result_message_id IS NULL)",
            name="result_message_matches_success",
        ),
        Index(
            "ix_generation_jobs_user_active",
            "telegram_user_id",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index(
            "ix_generation_jobs_user_created",
            "telegram_user_id",
            "created_at",
        ),
        Index("ix_generation_jobs_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    client_submission_uuid: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    telegram_file_unique_id: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
    )
    source_media_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_width: Mapped[int] = mapped_column(Integer, nullable=False)
    source_height: Mapped[int] = mapped_column(Integer, nullable=False)
    intent_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    palette_color_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    custom_color_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "custom_color_versions.id",
            name="fk_generation_jobs_custom_version",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    custom_color_sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    intent_display_name: Mapped[str] = mapped_column(String(40), nullable=False)
    image_model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="queued",
        server_default="queued",
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(240), nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    result_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    custom_reference_released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class GenerationAttempt(Base):
    """Bounded execution metadata for one worker claim."""

    __tablename__ = "generation_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_number"),
        CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        CheckConstraint(
            "worker_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$'",
            name="worker_id_safe",
        ),
        CheckConstraint(
            "state IN ('claimed', 'source_ready', 'provider_started', "
            "'provider_succeeded', 'delivering', 'succeeded', 'failed', "
            "'ambiguous')",
            name="state_supported",
        ),
        CheckConstraint(
            "safe_preupload_retries BETWEEN 0 AND 1",
            name="safe_preupload_retries_range",
        ),
        CheckConstraint(
            "provider_status_code IS NULL OR provider_status_code BETWEEN 100 AND 599",
            name="provider_status_code_range",
        ),
        CheckConstraint(
            "provider_latency_ms IS NULL OR provider_latency_ms >= 0",
            name="provider_latency_nonnegative",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="input_tokens_nonnegative",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="output_tokens_nonnegative",
        ),
        CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="total_tokens_nonnegative",
        ),
        CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0",
            name="cost_nonnegative",
        ),
        CheckConstraint(
            "output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'",
            name="output_sha256_hex",
        ),
        CheckConstraint(
            "(state = 'succeeded' AND result_message_id > 0 "
            "AND completed_at IS NOT NULL) "
            "OR (state <> 'succeeded' AND result_message_id IS NULL)",
            name="receipt_matches_success",
        ),
        CheckConstraint(
            "(state IN ('failed', 'ambiguous') AND error_code IS NOT NULL "
            "AND completed_at IS NOT NULL) "
            "OR (state NOT IN ('failed', 'ambiguous') "
            "AND error_code IS NULL AND error_summary IS NULL)",
            name="error_matches_terminal_state",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="error_code_safe",
        ),
        CheckConstraint("updated_at >= started_at", name="updated_after_started"),
        Index(
            "uq_generation_attempts_one_provider_start",
            "job_id",
            unique=True,
            postgresql_where=text("provider_started_at IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    job_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    safe_preupload_retries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    provider_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_latency_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    output_byte_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_format: Mapped[str | None] = mapped_column(String(8), nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delivery_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(240), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class JobOutbox(Base):
    """Recoverable UUID-only dispatch intent for one accepted job."""

    __tablename__ = "job_outbox"
    __table_args__ = (
        CheckConstraint(
            "publish_attempts >= 0",
            name="publish_attempts_nonnegative",
        ),
        CheckConstraint(
            "(publish_attempts = 0 AND last_attempt_at IS NULL) "
            "OR (publish_attempts > 0 AND last_attempt_at IS NOT NULL)",
            name="attempt_time_consistent",
        ),
        CheckConstraint(
            "published_at IS NULL OR publish_attempts > 0",
            name="published_has_attempt",
        ),
        CheckConstraint(
            "published_at IS NULL OR published_at >= created_at",
            name="published_after_created",
        ),
        CheckConstraint(
            "last_attempt_at IS NULL OR last_attempt_at >= created_at",
            name="attempt_after_created",
        ),
        Index(
            "ix_job_outbox_pending",
            "created_at",
            "job_id",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    job_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("generation_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    publish_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ModerationAttempt(Base):
    """Bounded structured moderation decision without media or raw prose."""

    __tablename__ = "moderation_attempts"
    __table_args__ = (
        UniqueConstraint("custom_color_version_id", "idempotency_key"),
        CheckConstraint(
            "decision IN ('approved', 'rejected', 'needs_review')",
            name="decision_supported",
        ),
        CheckConstraint(
            "safety_confidence BETWEEN 0 AND 10000",
            name="safety_confidence_range",
        ),
        CheckConstraint(
            "domain_confidence BETWEEN 0 AND 10000",
            name="domain_confidence_range",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    custom_color_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("custom_color_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_model: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    safety_confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    domain_confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AdminAuditEvent(Base):
    """Append-only record of an administrator mutation."""

    __tablename__ = "admin_audit_events"
    __table_args__ = (
        CheckConstraint(
            "actor_telegram_user_id > 0",
            name="actor_telegram_user_id_positive",
        ),
        CheckConstraint(
            "action IN ('approve', 'reject', 'rename', 'hide', 'restore', 'delete')",
            name="action_supported",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    actor_telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    custom_color_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("custom_colors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
