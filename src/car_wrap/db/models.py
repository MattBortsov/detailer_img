"""Metadata-only ownership and Mini App session models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
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
