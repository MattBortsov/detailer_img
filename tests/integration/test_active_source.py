"""Monotonic active-source selection on PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from car_wrap.bot.media import AcceptedMedia
from car_wrap.db.models import ActiveSource
from car_wrap.db.session import create_session_factory
from car_wrap.services.active_source import set_active_source

pytestmark = [pytest.mark.postgresql, pytest.mark.asyncio]


def candidate(
    suffix: str,
    *,
    width: int = 1200,
) -> AcceptedMedia:
    return AcceptedMedia(
        telegram_file_id=f"file-{suffix}",
        telegram_file_unique_id=f"unique-{suffix}",
        media_kind="photo",
        mime_type="image/jpeg",
        byte_size=2048,
        width=width,
        height=800,
    )


@pytest_asyncio.fixture
async def sessions(
    database_engine: AsyncEngine,
    test_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    del database_engine
    engine, factory = create_session_factory(
        test_database_url,
        poolclass=NullPool,
    )
    try:
        yield factory
    finally:
        await engine.dispose()


async def persist(
    sessions: async_sessionmaker[AsyncSession],
    media: AcceptedMedia,
    *,
    user_id: int,
    message_id: int,
) -> tuple[bool, ActiveSource]:
    async with sessions() as session:
        decision = await set_active_source(
            session,
            media,
            telegram_user_id=user_id,
            chat_id=user_id,
            source_message_id=message_id,
        )
        await session.commit()
        return decision.became_active, decision.active_source


async def test_first_source_wins_and_greater_message_replaces_all_fields(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    first_won, first = await persist(
        sessions,
        candidate("old", width=900),
        user_id=1001,
        message_id=10,
    )
    newer_won, newer = await persist(
        sessions,
        candidate("new", width=1400),
        user_id=1001,
        message_id=11,
    )

    assert first_won is True
    assert first.source_message_id == 10
    assert newer_won is True
    assert newer.source_message_id == 11
    assert newer.telegram_file_id == "file-new"
    assert newer.telegram_file_unique_id == "unique-new"
    assert newer.width == 1400


async def test_older_completion_cannot_overwrite_newer_source(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await persist(
        sessions,
        candidate("new"),
        user_id=1001,
        message_id=20,
    )
    older_won, canonical = await persist(
        sessions,
        candidate("old", width=600),
        user_id=1001,
        message_id=19,
    )

    assert older_won is False
    assert canonical.source_message_id == 20
    assert canonical.telegram_file_id == "file-new"
    assert canonical.width == 1200


async def test_equal_message_is_idempotent_and_does_not_mix_fields(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await persist(
        sessions,
        candidate("canonical", width=1000),
        user_id=1001,
        message_id=30,
    )
    duplicate_won, canonical = await persist(
        sessions,
        candidate("duplicate", width=1500),
        user_id=1001,
        message_id=30,
    )

    assert duplicate_won is True
    assert canonical.telegram_file_id == "file-canonical"
    assert canonical.width == 1000


async def test_concurrent_candidates_converge_on_greatest_message(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await asyncio.gather(
        persist(
            sessions,
            candidate("older"),
            user_id=1001,
            message_id=40,
        ),
        persist(
            sessions,
            candidate("newest"),
            user_id=1001,
            message_id=42,
        ),
        persist(
            sessions,
            candidate("middle"),
            user_id=1001,
            message_id=41,
        ),
    )

    older_won, canonical = await persist(
        sessions,
        candidate("probe"),
        user_id=1001,
        message_id=1,
    )
    assert older_won is False
    assert canonical.source_message_id == 42
    assert canonical.telegram_file_id == "file-newest"


async def test_owners_do_not_interfere(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    first, second = await asyncio.gather(
        persist(
            sessions,
            candidate("one"),
            user_id=1001,
            message_id=50,
        ),
        persist(
            sessions,
            candidate("two"),
            user_id=2002,
            message_id=2,
        ),
    )

    assert first[1].telegram_user_id == 1001
    assert second[1].telegram_user_id == 2002
