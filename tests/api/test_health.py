"""Production health probe contracts."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from car_wrap.api.app import create_app
from car_wrap.config import AppSettings


class FakeSession:
    def __init__(self, result: int | Exception) -> None:
        self.result = result

    async def scalar(self, statement: object) -> int:
        assert str(statement) == "SELECT 1"
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class SessionContext:
    def __init__(self, result: int | Exception) -> None:
        self.session = FakeSession(result)

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeSessions:
    def __init__(self, result: int | Exception) -> None:
        self.result = result

    def __call__(self) -> SessionContext:
        return SessionContext(self.result)


def build_app(result: int | Exception = 1) -> Any:
    settings = AppSettings.model_validate(
        {
            "database_url": "postgresql+psycopg://user:pass@db/test",
            "bot_token": "secret-token-canary",
            "bot_username": "CarWrapBot",
            "mini_app_url": "https://wrap.example.com",
        }
    )
    return create_app(settings=settings, session_factory=FakeSessions(result))


@pytest.mark.asyncio
async def test_liveness_is_fixed_and_does_not_touch_database() -> None:
    app = build_app(RuntimeError("database-url-secret"))
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_readiness_proves_database_round_trip() -> None:
    app = build_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("postgresql://user:secret@db/private"), 0],
)
@pytest.mark.asyncio
async def test_readiness_failure_is_fixed_and_redacted(
    failure: int | Exception,
) -> None:
    app = build_app(failure)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert "secret" not in response.text
    assert "postgresql" not in response.text
