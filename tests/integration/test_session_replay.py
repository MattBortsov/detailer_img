"""PostgreSQL replay defense for Telegram initData exchange."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from urllib.parse import quote

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from starlette.requests import Request

from car_wrap.api.app import create_app
from car_wrap.api.dependencies import require_mini_app_session
from car_wrap.config import AppSettings
from car_wrap.db.models import MiniAppSession
from car_wrap.db.session import create_session_factory
from car_wrap.services.telegram_auth import (
    TelegramAuthenticationError,
    exchange_init_data,
)

pytestmark = [pytest.mark.postgresql, pytest.mark.asyncio]

BOT_TOKEN = "123456:test-token"  # noqa: S105
NOW = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)


def settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "database_url": "postgresql+psycopg://user:pass@db/test",
            "bot_token": BOT_TOKEN,
            "bot_username": "CarWrapBot",
            "mini_app_url": "https://wrap.example.com/app",
        }
    )


def signed_init_data() -> str:
    fields = {
        "auth_date": str(int(NOW.timestamp())),
        "query_id": "one-time-query",
        "user": json.dumps({"id": 1001}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256,
    ).digest()
    fields["hash"] = hmac.new(
        secret,
        check.encode(),
        hashlib.sha256,
    ).hexdigest()
    return "&".join(
        f"{quote(key, safe='')}={quote(value, safe='')}"
        for key, value in fields.items()
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


async def exchange_once(
    sessions: async_sessionmaker[AsyncSession],
    raw: str,
) -> bool:
    async with sessions() as session:
        try:
            await exchange_init_data(
                session,
                raw,
                settings=settings(),
                now=NOW,
            )
            await session.commit()
        except TelegramAuthenticationError:
            return False
    return True


async def test_sequential_replay_creates_only_one_session(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    raw = signed_init_data()
    assert await exchange_once(sessions, raw) is True
    assert await exchange_once(sessions, raw) is False

    async with sessions() as session:
        rows = list(await session.scalars(select(MiniAppSession)))
    assert len(rows) == 1
    serialized = repr(rows[0].__dict__)
    assert raw not in serialized
    assert "one-time-query" not in serialized


async def test_concurrent_replay_has_exactly_one_winner(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    raw = signed_init_data()
    outcomes = await asyncio.gather(
        exchange_once(sessions, raw),
        exchange_once(sessions, raw),
    )

    assert sorted(outcomes) == [False, True]


def request_with_cookie(app: object, raw_token: str) -> Request:
    return Request(
        {
            "type": "http",
            "app": app,
            "method": "GET",
            "scheme": "https",
            "path": "/api/v1/palette",
            "raw_path": b"/api/v1/palette",
            "query_string": b"",
            "headers": [
                (
                    b"cookie",
                    f"car_wrap_session={raw_token}".encode(),
                )
            ],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 443),
        }
    )


async def test_cookie_dependency_resolves_only_current_server_owner(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    valid_token = "A" * 43
    expired_token = "B" * 43
    revoked_token = "C" * 43
    async with sessions() as session:
        session.add_all(
            [
                MiniAppSession(
                    token_sha256=hashlib.sha256(valid_token.encode()).hexdigest(),
                    init_data_sha256="a" * 64,
                    telegram_user_id=1001,
                    auth_date=NOW,
                    created_at=NOW,
                    expires_at=NOW.replace(hour=11),
                    revoked_at=None,
                ),
                MiniAppSession(
                    token_sha256=hashlib.sha256(expired_token.encode()).hexdigest(),
                    init_data_sha256="b" * 64,
                    telegram_user_id=2002,
                    auth_date=NOW,
                    created_at=NOW.replace(hour=9),
                    expires_at=NOW,
                    revoked_at=None,
                ),
                MiniAppSession(
                    token_sha256=hashlib.sha256(revoked_token.encode()).hexdigest(),
                    init_data_sha256="c" * 64,
                    telegram_user_id=3003,
                    auth_date=NOW,
                    created_at=NOW,
                    expires_at=NOW.replace(hour=11),
                    revoked_at=NOW,
                ),
            ]
        )
        await session.commit()

    app = create_app(
        settings=settings(),
        session_factory=sessions,
        clock=lambda: NOW,
    )
    async with sessions() as session:
        current = await require_mini_app_session(
            request_with_cookie(app, valid_token),
            session,
        )
        assert current.telegram_user_id == 1001

    for rejected_token in (expired_token, revoked_token, "D" * 43):
        async with sessions() as session:
            with pytest.raises(HTTPException) as caught:
                await require_mini_app_session(
                    request_with_cookie(app, rejected_token),
                    session,
                )
        assert caught.value.status_code == 401
        assert caught.value.detail == "Unauthorized"
