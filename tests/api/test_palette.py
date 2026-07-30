"""Authenticated palette readiness and validation API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from car_wrap.api.app import create_app
from car_wrap.api.dependencies import (
    CurrentMiniAppSession,
    require_mini_app_session,
)
from car_wrap.config import AppSettings
from car_wrap.db.models import ActiveSource

NOW = datetime(2026, 7, 28, 10, 30, tzinfo=UTC)
SUBMISSION_ID = "6db32e02-9371-450c-851f-f187bea635d5"


def settings(*, admin_ids: tuple[int, ...] = ()) -> AppSettings:
    return AppSettings.model_validate(
        {
            "database_url": "postgresql+psycopg://user:pass@db/test",
            "bot_token": "token",
            "bot_username": "CarWrapBot",
            "mini_app_url": "https://wrap.example.com/app",
            "admin_telegram_user_ids": admin_ids,
        }
    )


def source() -> ActiveSource:
    return ActiveSource(
        telegram_user_id=1001,
        chat_id=1001,
        source_message_id=77,
        telegram_file_id="file-secret-canary",
        telegram_file_unique_id="unique-secret-canary",
        media_kind="photo",
        mime_type="image/jpeg",
        byte_size=1024,
        width=1200,
        height=800,
        accepted_at=NOW,
        updated_at=NOW,
    )


def jpeg() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (320, 256), (40, 50, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


def downloadable_source(data: bytes) -> ActiveSource:
    row = source()
    row.byte_size = len(data)
    row.width = 320
    row.height = 256
    return row


class FakeTelegramBot:
    def __init__(self, payload: bytes = b"") -> None:
        self.payload = payload
        self.downloaded: list[str] = []
        self.sent: list[dict[str, Any]] = []

    async def download(self, file: str, destination: Any) -> None:
        self.downloaded.append(file)
        destination.write(self.payload)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        **kwargs: Any,
    ) -> None:
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})


class FakeSession:
    def __init__(self, active_source: ActiveSource | None) -> None:
        self.active_source = active_source
        self.scalar_calls = 0

    async def scalar(self, statement: object) -> ActiveSource | None:
        del statement
        self.scalar_calls += 1
        return self.active_source


class SessionContext:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeSessions:
    def __init__(self, active_source: ActiveSource | None) -> None:
        self.session = FakeSession(active_source)

    def __call__(self) -> SessionContext:
        return SessionContext(self.session)


def build_app(
    active_source: ActiveSource | None,
    *,
    admin_ids: tuple[int, ...] = (),
    telegram_bot: FakeTelegramBot | None = None,
) -> tuple[Any, FakeSessions]:
    sessions = FakeSessions(active_source)
    app = create_app(
        settings=settings(admin_ids=admin_ids),
        session_factory=sessions,
        clock=lambda: NOW,
        telegram_bot=telegram_bot,
    )
    app.dependency_overrides[require_mini_app_session] = lambda: CurrentMiniAppSession(
        telegram_user_id=1001,
        expires_at=NOW + timedelta(minutes=15),
    )
    return app, sessions


@pytest.mark.asyncio
async def test_palette_state_exposes_only_safe_ordered_owner_state() -> None:
    app, sessions = build_app(source())
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/palette-state")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["palette_version"] == "1"
    assert payload["source_ready"] is True
    assert payload["source_message_id"] == 77
    assert payload["source_preview_url"] == "/api/v1/active-source/image"
    assert payload["bot_chat_url"] == "https://t.me/CarWrapBot"
    assert payload["is_admin"] is False
    assert payload["privacy_text"] == (
        "Приложение не сохраняет файлы изображений. Telegram и AI-провайдер "
        "обрабатывают фото для создания визуализации."
    )
    assert [choice["color_id"] for choice in payload["choices"]] == [
        "pearl-white",
        "charcoal",
        "deep-blue",
        "warm-red",
        "forest-green",
        "copper",
        "bright-yellow",
        "violet",
        "surprise_me",
    ]
    assert payload["choices"][-1] == {
        "color_id": "surprise_me",
        "name": "Удиви меня",
        "display_hex": None,
        "kind": "surprise",
    }
    rendered = response.text
    for forbidden in (
        "telegram_user_id",
        "chat_id",
        "file-secret-canary",
        "unique-secret-canary",
        "mime_type",
        "byte_size",
        "width",
        "height",
        "model",
        "prompt",
        "provider",
        "token",
        "digest",
        "image_url",
    ):
        assert forbidden not in rendered
    assert sessions.session.scalar_calls == 1


@pytest.mark.asyncio
async def test_palette_state_marks_configured_admin_without_exposing_identity() -> None:
    app, _ = build_app(source(), admin_ids=(1001,))
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/palette-state")

    assert response.status_code == 200
    assert response.json()["is_admin"] is True
    assert "telegram_user_id" not in response.text


@pytest.mark.asyncio
async def test_palette_state_without_source_is_explicit_and_query_fails() -> None:
    app, _ = build_app(None)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/palette-state")
        injected = await client.get(
            "/api/v1/palette-state",
            params={"telegram_user_id": "9999"},
        )

    assert response.status_code == 200
    assert response.json()["source_ready"] is False
    assert response.json()["source_message_id"] is None
    assert response.json()["source_preview_url"] is None
    assert injected.status_code == 400


@pytest.mark.asyncio
async def test_active_source_image_streams_owner_photo_without_storage() -> None:
    payload = jpeg()
    telegram = FakeTelegramBot(payload)
    app, _ = build_app(downloadable_source(payload), telegram_bot=telegram)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/active-source/image")
        injected = await client.get(
            "/api/v1/active-source/image",
            params={"telegram_user_id": "2002"},
        )

    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == "inline"
    assert telegram.downloaded == ["file-secret-canary"]
    assert injected.status_code == 400


@pytest.mark.asyncio
async def test_replacement_request_prompts_same_owner_with_cancel() -> None:
    telegram = FakeTelegramBot()
    app, _ = build_app(source(), telegram_bot=telegram)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.post("/api/v1/active-source/replacement")

    assert response.status_code == 200
    assert response.json() == {
        "status": "prompt_sent",
        "bot_chat_url": "https://t.me/CarWrapBot",
    }
    assert telegram.sent[0]["chat_id"] == 1001
    assert telegram.sent[0]["text"] == "Пришлите новое фото"
    button = telegram.sent[0]["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Отмена"
    assert button.callback_data == "replace_photo:cancel"


@pytest.mark.parametrize("color_id", ["charcoal", "surprise_me"])
@pytest.mark.asyncio
async def test_selection_validation_accepts_only_catalog_intent(
    color_id: str,
) -> None:
    app, sessions = build_app(source())
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/palette-selection/validate",
            json={
                "color_id": color_id,
                "client_submission_uuid": SUBMISSION_ID,
            },
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["status"] == "validated"
    assert payload["palette_version"] == "1"
    assert payload["choice"]["color_id"] == color_id
    assert "client_submission_uuid" not in payload
    assert sessions.session.scalar_calls == 1


@pytest.mark.parametrize(
    "payload",
    [
        {
            "color_id": "unknown",
            "client_submission_uuid": SUBMISSION_ID,
        },
        {"color_id": "charcoal"},
        {
            "color_id": "charcoal",
            "client_submission_uuid": SUBMISSION_ID,
            "telegram_user_id": 9999,
        },
        {
            "color_id": "charcoal",
            "client_submission_uuid": SUBMISSION_ID,
            "display_hex": "#000000",
        },
    ],
)
@pytest.mark.asyncio
async def test_unknown_missing_and_privileged_fields_fail_closed(
    payload: dict[str, object],
) -> None:
    app, _ = build_app(source())
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/palette-selection/validate",
            json=payload,
        )

    assert response.status_code in {409, 422}
    assert "9999" not in response.text
    assert "#000000" not in response.text


@pytest.mark.asyncio
async def test_selection_requires_current_owner_source() -> None:
    app, _ = build_app(None)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/palette-selection/validate",
            json={
                "color_id": "charcoal",
                "client_submission_uuid": SUBMISSION_ID,
            },
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "Active source is unavailable"}
