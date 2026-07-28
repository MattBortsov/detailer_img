"""HTTPS static mounting and browser/API security policy."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient

from car_wrap.api.app import create_app
from car_wrap.config import AppSettings

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
EXPECTED_CSP = (
    "default-src 'self'; script-src 'self' https://telegram.org; "
    "style-src 'self'; img-src 'self'; connect-src 'self'; "
    "base-uri 'none'; object-src 'none'; form-action 'self'"
)


def settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "database_url": "postgresql+psycopg://user:pass@db/test",
            "bot_token": "bot-token-canary",
            "bot_username": "CarWrapBot",
            "mini_app_url": "https://wrap.example.com/app/",
        }
    )


class EmptySession:
    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class EmptySessions:
    def __call__(self) -> EmptySession:
        return EmptySession()


def app() -> Any:
    return create_app(
        settings=settings(),
        session_factory=EmptySessions(),
        clock=lambda: NOW,
    )


def assert_security_headers(response: Any) -> None:
    assert response.headers["content-security-policy"] == EXPECTED_CSP
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["permissions-policy"] == (
        "camera=(), microphone=(), geolocation=()"
    )
    assert "unsafe-inline" not in response.headers["content-security-policy"]
    assert "unsafe-eval" not in response.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_https_mount_serves_shell_css_and_modules_with_headers() -> None:
    application = app()
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="https://wrap.example.com",
    ) as client:
        page = await client.get("/app/")
        css = await client.get("/app/app.css")
        javascript = await client.get("/app/app.js")
        state_module = await client.get("/app/state.js")

    assert page.status_code == 200
    assert 'lang="ru"' in page.text
    assert css.status_code == 200
    assert javascript.status_code == 200
    assert state_module.status_code == 200
    for response in (page, css, javascript, state_module):
        assert_security_headers(response)


@pytest.mark.asyncio
async def test_insecure_request_redirects_to_same_https_target() -> None:
    application = app()
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://wrap.example.com",
        follow_redirects=False,
    ) as client:
        response = await client.get("/app/")

    assert response.status_code in {307, 308}
    assert response.headers["location"] == "https://wrap.example.com/app/"
    assert_security_headers(response)


@pytest.mark.asyncio
async def test_api_and_missing_asset_are_no_store_and_sanitized() -> None:
    application = app()
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="https://wrap.example.com",
    ) as client:
        api = await client.get("/api/v1/palette-state")
        missing = await client.get("/app/missing-secret-canary")

    assert api.status_code == 401
    assert api.headers["cache-control"] == "no-store"
    assert api.json() == {"detail": "Unauthorized"}
    assert missing.status_code == 404
    assert "missing-secret-canary" not in missing.text
    assert_security_headers(api)
    assert_security_headers(missing)


@pytest.mark.asyncio
async def test_internal_error_body_is_fixed_and_secret_free() -> None:
    application = app()

    async def broken(request: Request) -> None:
        del request
        raise RuntimeError("database-url-token-file-canary")

    application.add_api_route("/api/v1/broken", broken)
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="https://wrap.example.com",
    ) as client:
        response = await client.get("/api/v1/broken")

    assert response.status_code == 500
    assert response.json() == {"detail": "Service unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert "database-url-token-file-canary" not in response.text
    assert_security_headers(response)
