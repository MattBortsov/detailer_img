"""Metadata-only ownership and Mini App session models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    String,
    UniqueConstraint,
    func,
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
