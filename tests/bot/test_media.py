"""Bounded Telegram media intake tests."""

from __future__ import annotations

from io import BytesIO
from typing import Any

import pytest
from aiogram.types import Document, PhotoSize
from PIL import Image

from car_wrap.bot.media import (
    AcceptedMedia,
    MediaRejection,
    MediaRejectionCode,
    read_supported_media,
)
from car_wrap.config import AppSettings


def settings(**overrides: object) -> AppSettings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://user:pass@db/test",
        "bot_token": "token",
        "bot_username": "CarWrapBot",
        "mini_app_url": "https://wrap.example.com/app",
        "max_media_bytes": 1024 * 1024,
        "min_side_px": 16,
        "max_side_px": 512,
        "max_pixels": 512 * 512,
    }
    values.update(overrides)
    return AppSettings.model_validate(values)


def image_bytes(
    image_format: str = "JPEG",
    *,
    size: tuple[int, int] = (64, 48),
) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (70, 90, 110)).save(buffer, format=image_format)
    return buffer.getvalue()


class FakeDownloader:
    def __init__(
        self,
        payload: bytes,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.failure = failure
        self.calls = 0

    async def download(
        self,
        file: str,
        destination: Any,
    ) -> None:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        destination.write(self.payload)


def photo(*, file_size: int | None = None) -> PhotoSize:
    return PhotoSize(
        file_id="photo-file-id",
        file_unique_id="photo-unique-id",
        width=64,
        height=48,
        file_size=file_size,
    )


def document(
    mime_type: str = "image/png",
    *,
    file_size: int | None = None,
) -> Document:
    return Document(
        file_id="document-file-id",
        file_unique_id="document-unique-id",
        file_name="vehicle.png",
        mime_type=mime_type,
        file_size=file_size,
    )


@pytest.mark.asyncio
async def test_accepts_photo_as_jpeg_and_returns_metadata_only() -> None:
    downloader = FakeDownloader(image_bytes())

    accepted = await read_supported_media(
        downloader,
        photo(),
        settings=settings(),
    )

    assert accepted == AcceptedMedia(
        telegram_file_id="photo-file-id",
        telegram_file_unique_id="photo-unique-id",
        media_kind="photo",
        mime_type="image/jpeg",
        byte_size=len(downloader.payload),
        width=64,
        height=48,
    )
    assert not hasattr(accepted, "data")
    assert "JFIF" not in repr(accepted)


@pytest.mark.asyncio
async def test_accepts_supported_document_when_mime_matches_signature() -> None:
    downloader = FakeDownloader(image_bytes("PNG"))

    accepted = await read_supported_media(
        downloader,
        document(),
        settings=settings(),
    )

    assert accepted.media_kind == "document"
    assert accepted.mime_type == "image/png"


@pytest.mark.asyncio
async def test_declared_oversize_fails_before_download() -> None:
    downloader = FakeDownloader(image_bytes())

    with pytest.raises(MediaRejection) as caught:
        await read_supported_media(
            downloader,
            photo(file_size=1025),
            settings=settings(max_media_bytes=1024),
        )

    assert caught.value.code is MediaRejectionCode.TOO_LARGE
    assert downloader.calls == 0


@pytest.mark.asyncio
async def test_observed_bytes_cannot_bypass_cap() -> None:
    downloader = FakeDownloader(b"x" * 1025)

    with pytest.raises(MediaRejection) as caught:
        await read_supported_media(
            downloader,
            photo(file_size=1),
            settings=settings(max_media_bytes=1024),
        )

    assert caught.value.code is MediaRejectionCode.TOO_LARGE


@pytest.mark.parametrize(
    ("media", "payload", "code"),
    [
        (
            document("image/gif"),
            image_bytes("PNG"),
            MediaRejectionCode.UNSUPPORTED_FORMAT,
        ),
        (
            document("image/jpeg"),
            image_bytes("PNG"),
            MediaRejectionCode.UNSUPPORTED_FORMAT,
        ),
        (
            document("image/png"),
            b"not-an-image",
            MediaRejectionCode.UNREADABLE,
        ),
        (
            document("image/png"),
            image_bytes("PNG", size=(8, 64)),
            MediaRejectionCode.DIMENSION_LIMIT,
        ),
        (
            document("image/png"),
            image_bytes("PNG", size=(129, 129)),
            MediaRejectionCode.PIXEL_LIMIT,
        ),
    ],
)
@pytest.mark.asyncio
async def test_rejects_hostile_or_mismatched_media(
    media: Document,
    payload: bytes,
    code: MediaRejectionCode,
) -> None:
    limits = settings(max_side_px=256, max_pixels=16_384)

    with pytest.raises(MediaRejection) as caught:
        await read_supported_media(
            FakeDownloader(payload),
            media,
            settings=limits,
        )

    assert caught.value.code is code
    assert repr(caught.value) == f"MediaRejection(code={code.value!r})"


@pytest.mark.asyncio
async def test_maps_external_download_failure_to_safe_code() -> None:
    downloader = FakeDownloader(
        b"",
        failure=RuntimeError("telegram-secret-canary"),
    )

    with pytest.raises(MediaRejection) as caught:
        await read_supported_media(
            downloader,
            photo(),
            settings=settings(),
        )

    assert caught.value.code is MediaRejectionCode.DOWNLOAD_FAILED
    assert "telegram-secret-canary" not in repr(caught.value)
    assert caught.value.__cause__ is None
