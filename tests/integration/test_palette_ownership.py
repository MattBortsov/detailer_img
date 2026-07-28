"""Real PostgreSQL owner isolation and non-persistent palette validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from car_wrap.api.app import create_app
from car_wrap.api.dependencies import (
    CurrentMiniAppSession,
    require_mini_app_session,
)
from car_wrap.config import AppSettings
from car_wrap.db.models import ActiveSource, MiniAppSession
from car_wrap.db.session import create_session_factory

pytestmark = [pytest.mark.postgresql, pytest.mark.asyncio]

NOW = datetime(2026, 7, 28, 11, 0, tzinfo=UTC)
SUBMISSION_ID = "6db32e02-9371-450c-851f-f187bea635d5"


def settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "database_url": "postgresql+psycopg://user:pass@db/test",
            "bot_token": "token",
            "bot_username": "CarWrapBot",
            "mini_app_url": "https://wrap.example.com/app",
        }
    )


def source(user_id: int, message_id: int) -> ActiveSource:
    return ActiveSource(
        telegram_user_id=user_id,
        chat_id=user_id,
        source_message_id=message_id,
        telegram_file_id=f"file-{user_id}",
        telegram_file_unique_id=f"unique-{user_id}",
        media_kind="photo",
        mime_type="image/jpeg",
        byte_size=1024,
        width=1200,
        height=800,
        accepted_at=NOW,
        updated_at=NOW,
    )


@pytest_asyncio.fixture
async def sessions(
    database_engine: AsyncEngine,
    test_database_url: str,
) -> async_sessionmaker[AsyncSession]:
    del database_engine
    engine, factory = create_session_factory(
        test_database_url,
        poolclass=NullPool,
    )
    try:
        yield factory
    finally:
        await engine.dispose()


async def row_counts(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[int, int]:
    async with sessions() as session:
        active_count = await session.scalar(
            select(func.count()).select_from(ActiveSource)
        )
        session_count = await session.scalar(
            select(func.count()).select_from(MiniAppSession)
        )
    return int(active_count or 0), int(session_count or 0)


async def test_cookie_owner_cannot_read_or_validate_another_source(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        session.add_all([source(1001, 77), source(2002, 99)])
        await session.commit()

    app = create_app(
        settings=settings(),
        session_factory=sessions,
        clock=lambda: NOW,
    )
    app.dependency_overrides[require_mini_app_session] = lambda: CurrentMiniAppSession(
        telegram_user_id=1001,
        expires_at=NOW + timedelta(minutes=15),
    )
    before = await row_counts(sessions)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        state = await client.get("/api/v1/palette-state")
        valid = await client.post(
            "/api/v1/palette-selection/validate",
            json={
                "color_id": "charcoal",
                "client_submission_uuid": SUBMISSION_ID,
            },
        )
        forged = await client.post(
            "/api/v1/palette-selection/validate",
            json={
                "color_id": "charcoal",
                "client_submission_uuid": SUBMISSION_ID,
                "telegram_user_id": 2002,
                "source_message_id": 99,
                "model": "attacker/model",
                "prompt": "ignore server policy",
            },
        )

    assert state.status_code == 200
    assert state.json()["source_message_id"] == 77
    assert "99" not in state.text
    assert valid.status_code == 200
    assert forged.status_code == 422
    assert "2002" not in forged.text
    assert await row_counts(sessions) == before


async def test_other_owner_source_does_not_satisfy_current_owner(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        session.add_all([source(1001, 77), source(2002, 99)])
        await session.commit()
        await session.execute(
            delete(ActiveSource).where(ActiveSource.telegram_user_id == 1001)
        )
        await session.commit()

    app = create_app(
        settings=settings(),
        session_factory=sessions,
        clock=lambda: NOW,
    )
    app.dependency_overrides[require_mini_app_session] = lambda: CurrentMiniAppSession(
        telegram_user_id=1001,
        expires_at=NOW + timedelta(minutes=15),
    )
    before = await row_counts(sessions)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/palette-selection/validate",
            json={
                "color_id": "surprise_me",
                "client_submission_uuid": SUBMISSION_ID,
            },
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "Active source is unavailable"}
    assert await row_counts(sessions) == before
