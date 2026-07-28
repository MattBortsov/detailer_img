"""FastAPI one-time Telegram session boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from car_wrap.api.app import create_app
from car_wrap.config import AppSettings
from car_wrap.services.telegram_auth import (
    IssuedMiniAppSession,
    TelegramAuthenticationError,
)

NOW = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)


def settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "database_url": "postgresql+psycopg://user:pass@db/test",
            "bot_token": "bot-token-canary",
            "bot_username": "CarWrapBot",
            "mini_app_url": "https://wrap.example.com/app",
            "init_data_max_bytes": 128,
            "session_ttl_seconds": 900,
        }
    )


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class SessionContext:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeSessions:
    def __init__(self) -> None:
        self.session = FakeSession()

    def __call__(self) -> SessionContext:
        return SessionContext(self.session)


async def successful_exchange(
    session: object,
    raw_init_data: str,
    *,
    settings: AppSettings,
    now: datetime,
) -> IssuedMiniAppSession:
    del session, settings
    assert raw_init_data == "signed-launch-canary"
    return IssuedMiniAppSession(
        token=SecretStr("A" * 43),
        telegram_user_id=1001,
        expires_at=now + timedelta(minutes=15),
    )


async def rejected_exchange(*args: Any, **kwargs: Any) -> IssuedMiniAppSession:
    del args, kwargs
    raise TelegramAuthenticationError


def build_app(exchange: Any = successful_exchange) -> tuple[Any, FakeSessions]:
    sessions = FakeSessions()
    app = create_app(
        settings=settings(),
        session_factory=sessions,
        clock=lambda: NOW,
        exchange_service=exchange,
    )
    return app, sessions


@pytest.mark.asyncio
async def test_valid_exchange_sets_only_secure_opaque_cookie() -> None:
    app, sessions = build_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/tma/session",
            headers={"Authorization": "tma signed-launch-canary"},
        )

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert f"car_wrap_session={'A' * 43}" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/api/v1" in cookie
    assert "signed-launch-canary" not in cookie
    assert sessions.session.commits == 1


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer signed-launch-canary"},
        {"Authorization": "tma "},
        {"Authorization": "tma " + "x" * 129},
    ],
)
@pytest.mark.asyncio
async def test_malformed_authorization_is_one_safe_unauthorized(
    headers: dict[str, str],
) -> None:
    app, sessions = build_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/tma/session",
            headers=headers,
            json={"initData": "signed-launch-canary"},
            params={"telegram_user_id": "9999"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}
    assert response.headers["cache-control"] == "no-store"
    rendered = response.text + repr(response.headers)
    assert "signed-launch-canary" not in rendered
    assert "9999" not in rendered
    assert sessions.session.commits == 0


@pytest.mark.asyncio
async def test_duplicate_authorization_headers_fail_closed() -> None:
    app, sessions = build_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/tma/session",
            headers=[
                ("Authorization", "tma signed-launch-canary"),
                ("Authorization", "tma second"),
            ],
        )

    assert response.status_code == 401
    assert sessions.session.commits == 0


@pytest.mark.asyncio
async def test_authentication_failure_never_sets_cookie_or_leaks_input() -> None:
    app, sessions = build_app(rejected_exchange)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/tma/session",
            headers={"Authorization": "tma tampered-canary"},
        )

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert "tampered-canary" not in response.text
    assert sessions.session.commits == 0
