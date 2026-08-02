"""Strict authenticated custom color upload API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from car_wrap.api.app import create_app
from car_wrap.api.dependencies import (
    CurrentMiniAppSession,
    require_mini_app_session,
)
from car_wrap.config import AppSettings
from car_wrap.custom_colors.repository import QuotaExceededError

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


def settings(*, admins: tuple[int, ...] = ()) -> AppSettings:
    return AppSettings.model_validate(
        {
            "database_url": "postgresql+psycopg://user:pass@db/test",
            "bot_token": "token",
            "bot_username": "CarWrapBot",
            "mini_app_url": "https://wrap.example.com/app",
            "admin_telegram_user_ids": admins,
        }
    )


class FakeSession:
    async def scalar(self, statement: object) -> None:
        del statement
        return None


class SessionContext:
    async def __aenter__(self) -> FakeSession:
        return FakeSession()

    async def __aexit__(self, *args: object) -> None:
        return None


class Sessions:
    def __call__(self) -> SessionContext:
        return SessionContext()


@dataclass
class Created:
    id: Any
    display_name: str
    status: str
    current_version: int


class Service:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, session: object, **kwargs: Any) -> Created:
        del session
        self.calls.append(kwargs)
        return Created(uuid4(), "Bronze Satin", "needs_review", 1)


class QuotaService(Service):
    async def create(self, session: object, **kwargs: Any) -> Created:
        del session, kwargs
        raise QuotaExceededError("private quota detail")


def app_with(service: Service, *, authenticated: bool = True) -> Any:
    app = create_app(
        settings=settings(),
        session_factory=Sessions(),
        clock=lambda: NOW,
        custom_color_service=service,
    )
    if authenticated:
        app.dependency_overrides[require_mini_app_session] = lambda: (
            CurrentMiniAppSession(
                telegram_user_id=1001,
                expires_at=NOW + timedelta(minutes=15),
            )
        )
    return app


@pytest.mark.asyncio
async def test_creation_accepts_exactly_name_and_one_image() -> None:
    service = Service()
    async with AsyncClient(
        transport=ASGITransport(app=app_with(service)),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/custom-colors",
            headers={"Idempotency-Key": "upload-1"},
            files={
                "name": (None, "Bronze Satin"),
                "image": ("sample.png", b"\x89PNG\r\n\x1a\nbytes", "image/png"),
            },
        )
    assert response.status_code == 202
    assert response.json()["status"] == "needs_review"
    assert service.calls[0]["owner_id"] == 1001
    assert service.calls[0]["color_structure"] == "unspecified"
    assert service.calls[0]["finish"] == "unspecified"
    assert "telegram_user_id" not in response.text


@pytest.mark.asyncio
async def test_creation_accepts_explicit_structure_and_finish() -> None:
    service = Service()
    async with AsyncClient(
        transport=ASGITransport(app=app_with(service)),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/custom-colors",
            headers={"Idempotency-Key": "upload-profiled"},
            files={
                "name": (None, "Dream Grey Charm Purple"),
                "color_structure": (None, "multicolor"),
                "finish": (None, "gloss"),
                "image": ("sample.png", b"\x89PNG\r\n\x1a\nbytes", "image/png"),
            },
        )

    assert response.status_code == 202
    assert service.calls[0]["color_structure"] == "multicolor"
    assert service.calls[0]["finish"] == "gloss"


@pytest.mark.parametrize(
    "files",
    (
        {"image": ("sample.png", b"bytes", "image/png")},
        {
            "name": (None, "Bronze"),
            "image": ("sample.png", b"bytes", "image/png"),
            "owner_id": (None, "9999"),
        },
        {
            "name": (None, "Bronze"),
            "image": ("sample.gif", b"bytes", "image/gif"),
        },
    ),
)
@pytest.mark.asyncio
async def test_missing_extra_and_unsupported_fields_fail(
    files: dict[str, tuple[Any, ...]],
) -> None:
    service = Service()
    async with AsyncClient(
        transport=ASGITransport(app=app_with(service)),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/custom-colors",
            headers={"Idempotency-Key": "upload-1"},
            files=files,
        )
    assert response.status_code == 422
    assert not service.calls
    assert "9999" not in response.text


@pytest.mark.asyncio
async def test_creation_requires_authenticated_session() -> None:
    service = Service()
    async with AsyncClient(
        transport=ASGITransport(app=app_with(service, authenticated=False)),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/custom-colors",
            headers={"Idempotency-Key": "upload-1"},
            files={
                "name": (None, "Bronze"),
                "image": ("sample.png", b"bytes", "image/png"),
            },
        )
    assert response.status_code == 401
    assert not service.calls


@pytest.mark.asyncio
async def test_creation_returns_actionable_concealed_quota_error() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_with(QuotaService())),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/custom-colors",
            headers={"Idempotency-Key": "upload-1"},
            files={
                "name": (None, "Bronze"),
                "image": ("sample.png", b"bytes", "image/png"),
            },
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "Custom color quota reached"}
    assert "private quota detail" not in response.text


@pytest.mark.asyncio
async def test_non_admin_cannot_open_review_queue() -> None:
    service = Service()
    async with AsyncClient(
        transport=ASGITransport(app=app_with(service)),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/custom-colors/admin/review")
    assert response.status_code == 403
