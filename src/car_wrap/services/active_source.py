"""Atomic newest-Telegram-message active-source selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.bot.media import AcceptedMedia
from car_wrap.db.models import ActiveSource


@dataclass(frozen=True, slots=True)
class ActiveSourceDecision:
    """Canonical source after considering one validated candidate."""

    active_source: ActiveSource
    became_active: bool


async def set_active_source(
    session: AsyncSession,
    candidate: AcceptedMedia,
    *,
    telegram_user_id: int,
    chat_id: int,
    source_message_id: int,
) -> ActiveSourceDecision:
    """Conditionally replace a user's source only with a newer message."""

    now = datetime.now(UTC)
    statement = insert(ActiveSource).values(
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        source_message_id=source_message_id,
        telegram_file_id=candidate.telegram_file_id,
        telegram_file_unique_id=candidate.telegram_file_unique_id,
        media_kind=candidate.media_kind,
        mime_type=candidate.mime_type,
        byte_size=candidate.byte_size,
        width=candidate.width,
        height=candidate.height,
        accepted_at=now,
        updated_at=now,
    )
    excluded = statement.excluded
    statement = statement.on_conflict_do_update(
        index_elements=[ActiveSource.telegram_user_id],
        set_={
            "chat_id": excluded.chat_id,
            "source_message_id": excluded.source_message_id,
            "telegram_file_id": excluded.telegram_file_id,
            "telegram_file_unique_id": excluded.telegram_file_unique_id,
            "media_kind": excluded.media_kind,
            "mime_type": excluded.mime_type,
            "byte_size": excluded.byte_size,
            "width": excluded.width,
            "height": excluded.height,
            "accepted_at": excluded.accepted_at,
            "updated_at": excluded.updated_at,
        },
        where=ActiveSource.source_message_id < excluded.source_message_id,
    )
    await session.execute(statement)
    canonical = await session.scalar(
        select(ActiveSource)
        .where(ActiveSource.telegram_user_id == telegram_user_id)
        .execution_options(populate_existing=True)
    )
    if canonical is None:
        raise RuntimeError("active source invariant failed")
    return ActiveSourceDecision(
        active_source=canonical,
        became_active=canonical.source_message_id == source_message_id,
    )
