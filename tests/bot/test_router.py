"""Telegram router copy, ownership, and reply contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.types import Document, PhotoSize
from PIL import Image

from car_wrap.bot.router import (
    CUSTOM_COLOR_FINISH_COPY,
    CUSTOM_COLOR_REQUEST_COPY,
    CUSTOM_COLOR_STRUCTURE_COPY,
    NO_SOURCE_COPY,
    UNSUPPORTED_MESSAGE_COPY,
    CustomColorUploadState,
    create_router,
    handle_custom_color_finish,
    handle_custom_color_message,
    handle_custom_color_request,
    handle_custom_color_structure,
    handle_media_message,
    handle_menu_callback,
    handle_replace_photo_cancel_callback,
    handle_start_message,
    handle_unsupported_message,
)
from car_wrap.config import AppSettings
from car_wrap.db.models import ActiveSource
from car_wrap.services.active_source import ActiveSourceDecision


def settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "database_url": "postgresql+psycopg://user:pass@db/test",
            "bot_token": "token",
            "bot_username": "CarWrapBot",
            "mini_app_url": "https://wrap.example.com/app",
            "max_media_bytes": 1024 * 1024,
            "min_side_px": 16,
            "max_side_px": 512,
            "max_pixels": 512 * 512,
        }
    )


def jpeg() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 48), (40, 50, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


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
        self.calls = 0

    def __call__(self) -> SessionContext:
        self.calls += 1
        return SessionContext(self.session)


class FakeBot:
    def __init__(
        self,
        payload: bytes = b"",
        *,
        download_failure: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.download_failure = download_failure
        self.sent: list[dict[str, Any]] = []
        self.edited: list[dict[str, Any]] = []
        self.answered_callbacks: list[str] = []

    async def download(self, file: str, destination: Any) -> None:
        del file
        if self.download_failure is not None:
            raise self.download_failure
        destination.write(self.payload)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        **kwargs: Any,
    ) -> SimpleNamespace:
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return SimpleNamespace(message_id=100 + len(self.sent))

    async def edit_message_text(self, **kwargs: Any) -> None:
        self.edited.append(kwargs)

    async def answer_callback_query(self, callback_query_id: str) -> None:
        self.answered_callbacks.append(callback_query_id)


def message(
    *,
    message_id: int = 10,
    user_id: int | None = 1001,
    chat_id: int = 1001,
    chat_type: str = "private",
    media: PhotoSize | Document | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        message_id=message_id,
        from_user=(None if user_id is None else SimpleNamespace(id=user_id)),
        chat=SimpleNamespace(id=chat_id, type=chat_type),
        photo=[media] if isinstance(media, PhotoSize) else None,
        document=media if isinstance(media, Document) else None,
    )


def photo() -> PhotoSize:
    return PhotoSize(
        file_id="photo-file",
        file_unique_id="photo-unique",
        width=64,
        height=48,
        file_size=None,
    )


def active_source(message_id: int) -> ActiveSource:
    now = datetime.now(UTC)
    return ActiveSource(
        telegram_user_id=1001,
        chat_id=1001,
        source_message_id=message_id,
        telegram_file_id="photo-file",
        telegram_file_unique_id="photo-unique",
        media_kind="photo",
        mime_type="image/jpeg",
        byte_size=1024,
        width=64,
        height=48,
        accepted_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_start_and_unsupported_copy_are_exact() -> None:
    bot = FakeBot()
    incoming = message()

    await handle_start_message(incoming, bot=bot)
    await handle_unsupported_message(incoming, bot=bot)

    assert bot.sent[0]["text"] == NO_SOURCE_COPY
    assert bot.sent[1]["text"] == UNSUPPORTED_MESSAGE_COPY


@pytest.mark.asyncio
async def test_custom_color_flow_collects_two_independent_axes_before_file() -> None:
    bot = FakeBot(jpeg())
    sessions = FakeSessions()
    uploads: dict[int, CustomColorUploadState] = {}
    incoming = message()

    await handle_custom_color_request(
        incoming,
        bot=bot,
        pending_uploads=uploads,
    )
    assert bot.sent[-1]["text"] == CUSTOM_COLOR_STRUCTURE_COPY
    structure_buttons = bot.sent[-1]["reply_markup"].inline_keyboard[0]
    assert [button.text for button in structure_buttons] == [
        "Однотонная",
        "Многоцветная",
    ]

    await handle_custom_color_structure(
        SimpleNamespace(
            id="structure",
            data="custom_color:structure:multicolor",
            from_user=SimpleNamespace(id=1001),
            message=incoming,
        ),
        bot=bot,
        pending_uploads=uploads,
    )
    assert bot.sent[-1]["text"] == CUSTOM_COLOR_FINISH_COPY
    finish_buttons = bot.sent[-1]["reply_markup"].inline_keyboard[0]
    assert [button.text for button in finish_buttons] == [
        "Матовая",
        "Сатин",
        "Глянцевая",
    ]

    await handle_custom_color_finish(
        SimpleNamespace(
            id="finish",
            data="custom_color:finish:gloss",
            from_user=SimpleNamespace(id=1001),
            message=incoming,
        ),
        bot=bot,
        pending_uploads=uploads,
    )
    assert bot.sent[-1]["text"] == CUSTOM_COLOR_REQUEST_COPY
    assert uploads[1001] == CustomColorUploadState("multicolor", "gloss")

    class Service:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def create(self, session: object, **kwargs: Any) -> SimpleNamespace:
            del session
            self.calls.append(kwargs)
            return SimpleNamespace(
                id="9af2285a-7e61-4b7c-906f-a5bedcd5f313",
                display_name="Без названия",
                status="needs_review",
                current_version=1,
            )

    service = Service()
    document = Document(
        file_id="sample-file",
        file_unique_id="sample-unique",
        file_name="sample.jpg",
        mime_type="image/jpeg",
    )
    await handle_custom_color_message(
        message(media=document),
        bot=bot,
        settings=settings(),
        session_factory=sessions,
        custom_color_service=service,
        pending_uploads=uploads,
    )

    assert service.calls[0]["color_structure"] == "multicolor"
    assert service.calls[0]["finish"] == "gloss"
    assert 1001 not in uploads
    assert bot.edited[-1]["text"].startswith("Образец получен")


@pytest.mark.asyncio
async def test_structure_callback_initializes_flow_from_api_sent_prompt() -> None:
    bot = FakeBot()
    uploads: dict[int, CustomColorUploadState] = {}

    await handle_custom_color_structure(
        SimpleNamespace(
            id="structure-from-api",
            data="custom_color:structure:solid",
            from_user=SimpleNamespace(id=1001),
            message=message(),
        ),
        bot=bot,
        pending_uploads=uploads,
    )

    assert uploads[1001] == CustomColorUploadState("solid", None)
    assert bot.sent[-1]["text"] == CUSTOM_COLOR_FINISH_COPY


@pytest.mark.asyncio
async def test_menu_callback_reopens_palette_or_invites_new_photo() -> None:
    bot = FakeBot()
    callback = SimpleNamespace(
        id="callback-1",
        from_user=SimpleNamespace(id=1001),
        message=SimpleNamespace(chat=SimpleNamespace(id=1001, type="private")),
    )

    await handle_menu_callback(callback, bot=bot, settings=settings())

    assert bot.answered_callbacks == ["callback-1"]
    assert bot.sent[0]["text"] == (
        "Отправьте новое фото или выберите другой цвет для текущего."
    )
    button = bot.sent[0]["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Выбрать цвет"
    assert button.web_app.url == "https://wrap.example.com/app"


@pytest.mark.asyncio
async def test_replace_photo_cancel_sends_fresh_palette_message() -> None:
    bot = FakeBot()
    callback = SimpleNamespace(
        id="callback-cancel",
        from_user=SimpleNamespace(id=1001),
        message=SimpleNamespace(chat=SimpleNamespace(id=1001, type="private")),
    )

    await handle_replace_photo_cancel_callback(
        callback,
        bot=bot,
        settings=settings(),
    )

    assert bot.answered_callbacks == ["callback-cancel"]
    assert bot.sent[0]["text"] == "Теперь выберите цвет."
    button = bot.sent[0]["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Выбрать цвет"
    assert button.web_app.url == "https://wrap.example.com/app"


@pytest.mark.parametrize(
    ("became_active", "expected_text", "expected_button"),
    [
        (
            True,
            "Теперь выберите цвет.",
            "Выбрать цвет",
        ),
        (
            False,
            "Фото принято, но для оклейки уже выбрано более новое фото.",
            "Выбрать цвет для активного фото",
        ),
    ],
)
@pytest.mark.asyncio
async def test_media_reply_truthfully_identifies_winner(
    became_active: bool,
    expected_text: str,
    expected_button: str,
) -> None:
    bot = FakeBot(jpeg())
    sessions = FakeSessions()
    incoming = message(media=photo())

    async def setter(*args: Any, **kwargs: Any) -> ActiveSourceDecision:
        del args, kwargs
        return ActiveSourceDecision(
            active_source=active_source(10 if became_active else 11),
            became_active=became_active,
        )

    await handle_media_message(
        incoming,
        bot=bot,
        settings=settings(),
        session_factory=sessions,
        active_source_setter=setter,
    )

    assert sessions.calls == 1
    assert sessions.session.commits == 1
    sent = bot.sent[0]
    assert sent["text"] == expected_text
    assert sent["reply_to_message_id"] == 10
    button = sent["reply_markup"].inline_keyboard[0][0]
    assert button.text == expected_button
    assert button.web_app.url == "https://wrap.example.com/app"


@pytest.mark.parametrize(
    "incoming",
    [
        message(media=photo(), chat_type="group"),
        message(media=photo(), user_id=None),
        message(media=photo(), user_id=1001, chat_id=2002),
    ],
)
@pytest.mark.asyncio
async def test_untrusted_sender_context_does_not_download_or_write(
    incoming: SimpleNamespace,
) -> None:
    bot = FakeBot(jpeg())
    sessions = FakeSessions()

    await handle_media_message(
        incoming,
        bot=bot,
        settings=settings(),
        session_factory=sessions,
    )

    assert sessions.calls == 0
    assert bot.sent == []


@pytest.mark.asyncio
async def test_rejections_use_limit_specific_copy_without_database_write() -> None:
    bot = FakeBot(b"")
    sessions = FakeSessions()
    unsupported = Document(
        file_id="file",
        file_unique_id="unique",
        file_name="vehicle.gif",
        mime_type="image/gif",
    )

    await handle_media_message(
        message(media=unsupported),
        bot=bot,
        settings=settings(),
        session_factory=sessions,
    )

    assert sessions.calls == 0
    assert bot.sent[0]["text"] == (
        "Этот формат не поддерживается. Отправьте изображение "
        "в одном из форматов: JPEG, PNG, WebP."
    )


@pytest.mark.asyncio
async def test_download_failure_is_sanitized() -> None:
    bot = FakeBot(download_failure=RuntimeError("token-canary"))
    sessions = FakeSessions()

    await handle_media_message(
        message(media=photo()),
        bot=bot,
        settings=settings(),
        session_factory=sessions,
    )

    assert sessions.calls == 0
    assert bot.sent[0]["text"] == (
        "Не удалось получить фото из Telegram. Отправьте его ещё раз."
    )
    assert "token-canary" not in repr(bot.sent)


def test_router_registers_private_start_media_and_fallback_handlers() -> None:
    router = create_router(settings=settings(), session_factory=FakeSessions())
    assert len(router.message.handlers) == 3
    assert len(router.callback_query.handlers) == 6
