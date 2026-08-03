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
from car_wrap.bot.router import CUSTOM_COLOR_STRUCTURE_COPY
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


class EditService(Service):
    def __init__(self) -> None:
        super().__init__()
        self.edit_call: dict[str, Any] | None = None

    async def edit_details(self, session: object, **kwargs: Any) -> Created:
        del session
        self.edit_call = kwargs
        return Created(kwargs["color_id"], kwargs["display_name"], "approved", 1)


class FakeTelegramBot:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.sent: list[dict[str, Any]] = []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        **kwargs: Any,
    ) -> None:
        if self.failure is not None:
            raise self.failure
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})


def app_with(
    service: Service,
    *,
    authenticated: bool = True,
    telegram_bot: FakeTelegramBot | None = None,
    admins: tuple[int, ...] = (),
) -> Any:
    app = create_app(
        settings=settings(admins=admins),
        session_factory=Sessions(),
        clock=lambda: NOW,
        custom_color_service=service,
        telegram_bot=telegram_bot,
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
async def test_prompt_is_sent_to_authenticated_owner_before_chat_opens() -> None:
    telegram = FakeTelegramBot()
    async with AsyncClient(
        transport=ASGITransport(
            app=app_with(Service(), telegram_bot=telegram),
        ),
        base_url="https://testserver",
    ) as client:
        response = await client.post("/api/v1/custom-colors/prompt")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "status": "prompt_sent",
        "bot_chat_url": "https://t.me/CarWrapBot",
    }
    assert telegram.sent[0]["chat_id"] == 1001
    assert telegram.sent[0]["text"] == CUSTOM_COLOR_STRUCTURE_COPY
    buttons = telegram.sent[0]["reply_markup"].inline_keyboard[0]
    assert [button.text for button in buttons] == ["Однотонная", "Многоцветная"]
    assert [button.callback_data for button in buttons] == [
        "custom_color:structure:solid",
        "custom_color:structure:multicolor",
    ]


@pytest.mark.asyncio
async def test_prompt_reports_telegram_delivery_failure_without_chat_url() -> None:
    telegram = FakeTelegramBot(failure=RuntimeError("private telegram detail"))
    async with AsyncClient(
        transport=ASGITransport(
            app=app_with(Service(), telegram_bot=telegram),
        ),
        base_url="https://testserver",
    ) as client:
        response = await client.post("/api/v1/custom-colors/prompt")

    assert response.status_code == 502
    assert response.json() == {"detail": "Could not open custom color flow"}
    assert "private telegram detail" not in response.text


@pytest.mark.asyncio
async def test_prompt_requires_session_and_rejects_identity_query() -> None:
    unauthenticated_bot = FakeTelegramBot()
    authenticated_bot = FakeTelegramBot()
    async with (
        AsyncClient(
            transport=ASGITransport(
                app=app_with(
                    Service(),
                    authenticated=False,
                    telegram_bot=unauthenticated_bot,
                ),
            ),
            base_url="https://testserver",
        ) as unauthenticated,
        AsyncClient(
            transport=ASGITransport(
                app=app_with(Service(), telegram_bot=authenticated_bot),
            ),
            base_url="https://testserver",
        ) as authenticated,
    ):
        missing_session = await unauthenticated.post("/api/v1/custom-colors/prompt")
        injected_identity = await authenticated.post(
            "/api/v1/custom-colors/prompt",
            params={"telegram_user_id": "2002"},
        )

    assert missing_session.status_code == 401
    assert injected_identity.status_code == 400
    assert not unauthenticated_bot.sent
    assert not authenticated_bot.sent


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


@pytest.mark.asyncio
async def test_admin_can_edit_catalog_name_and_category_together() -> None:
    service = EditService()
    color_id = uuid4()
    async with AsyncClient(
        transport=ASGITransport(app=app_with(service, admins=(1001,))),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            f"/api/v1/custom-colors/admin/{color_id}/edit",
            json={
                "name": "Dream Grey Charm Purple",
                "color_structure": "multicolor",
                "finish": "gloss",
                "reason": "admin_edited_from_catalog",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"id": str(color_id), "status": "approved"}
    assert service.edit_call == {
        "color_id": color_id,
        "display_name": "Dream Grey Charm Purple",
        "color_structure": "multicolor",
        "finish": "gloss",
        "admin_actor_id": 1001,
        "admin_reason": "admin_edited_from_catalog",
    }


@pytest.mark.asyncio
async def test_non_admin_cannot_edit_catalog_details() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_with(EditService())),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            f"/api/v1/custom-colors/admin/{uuid4()}/edit",
            json={
                "name": "Dream Grey",
                "color_structure": "solid",
                "finish": "gloss",
            },
        )

    assert response.status_code == 403
