"""Bounded, memory-only validation of Telegram image media."""

from __future__ import annotations

import warnings
from collections.abc import Buffer
from dataclasses import dataclass, field
from enum import StrEnum
from io import BytesIO
from typing import Any, Literal, Protocol

from aiogram.types import Document, PhotoSize
from PIL import Image, UnidentifiedImageError

from car_wrap.config import AppSettings
from car_wrap.eval.image_validation import (
    ImageValidationError,
    validate_image_bytes,
)

MediaKind = Literal["photo", "document"]


class MediaRejectionCode(StrEnum):
    """Stable reasons that map to user-facing recovery copy."""

    UNSUPPORTED_FORMAT = "unsupported_format"
    TOO_LARGE = "too_large"
    DIMENSION_LIMIT = "dimension_limit"
    PIXEL_LIMIT = "pixel_limit"
    UNREADABLE = "unreadable"
    DOWNLOAD_FAILED = "download_failed"
    SOURCE_CHANGED = "source_changed"


class MediaRejection(ValueError):
    """A media failure with no raw external exception text."""

    def __init__(self, code: MediaRejectionCode) -> None:
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"MediaRejection(code={self.code.value!r})"


@dataclass(frozen=True, slots=True)
class AcceptedMedia:
    """Reusable Telegram references and fully validated scalar metadata."""

    telegram_file_id: str
    telegram_file_unique_id: str
    media_kind: MediaKind
    mime_type: str
    byte_size: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    """Validated source bytes whose repr cannot expose media content."""

    data: bytes = field(repr=False)
    mime_type: str
    byte_size: int
    width: int
    height: int


class TelegramDownloader(Protocol):
    async def download(
        self,
        file: str,
        destination: Any,
    ) -> object: ...


class CappedBytesIO(BytesIO):
    """BytesIO destination that refuses a write beyond its hard cap."""

    def __init__(self, maximum_bytes: int) -> None:
        super().__init__()
        self._maximum_bytes = maximum_bytes

    def write(self, data: Buffer, /) -> int:
        if self.tell() + memoryview(data).nbytes > self._maximum_bytes:
            raise MediaRejection(MediaRejectionCode.TOO_LARGE)
        return super().write(data)


def _observed_mime_type(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise MediaRejection(MediaRejectionCode.UNREADABLE)


def _inspect_dimensions(
    data: bytes,
    *,
    settings: AppSettings,
) -> tuple[int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
                frame_count = getattr(image, "n_frames", 1)
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        raise MediaRejection(MediaRejectionCode.UNREADABLE) from None
    if frame_count != 1:
        raise MediaRejection(MediaRejectionCode.UNREADABLE)
    if (
        width < settings.min_side_px
        or height < settings.min_side_px
        or width > settings.max_side_px
        or height > settings.max_side_px
    ):
        raise MediaRejection(MediaRejectionCode.DIMENSION_LIMIT)
    if width * height > settings.max_pixels:
        raise MediaRejection(MediaRejectionCode.PIXEL_LIMIT)
    return width, height


def _media_contract(
    media: PhotoSize | Document,
    settings: AppSettings,
) -> tuple[MediaKind, str]:
    if isinstance(media, PhotoSize):
        return "photo", "image/jpeg"
    if media.mime_type not in settings.document_mime_allowlist:
        raise MediaRejection(MediaRejectionCode.UNSUPPORTED_FORMAT)
    return "document", media.mime_type


async def read_supported_media(
    downloader: TelegramDownloader,
    media: PhotoSize | Document,
    *,
    settings: AppSettings,
) -> AcceptedMedia:
    """Download, fully validate, then discard one bounded Telegram image."""

    media_kind, declared_mime_type = _media_contract(media, settings)
    if media.file_size is not None and media.file_size > settings.max_media_bytes:
        raise MediaRejection(MediaRejectionCode.TOO_LARGE)

    downloaded = await download_validated_bytes(
        downloader,
        file_id=media.file_id,
        declared_mime_type=declared_mime_type,
        settings=settings,
    )
    return AcceptedMedia(
        telegram_file_id=media.file_id,
        telegram_file_unique_id=media.file_unique_id,
        media_kind=media_kind,
        mime_type=downloaded.mime_type,
        byte_size=downloaded.byte_size,
        width=downloaded.width,
        height=downloaded.height,
    )


async def read_snapshotted_media(
    downloader: TelegramDownloader,
    *,
    file_id: str,
    declared_mime_type: str,
    expected_byte_size: int,
    expected_width: int,
    expected_height: int,
    settings: AppSettings,
) -> DownloadedMedia:
    """Download a job source and require it to match its immutable snapshot."""

    downloaded = await download_validated_bytes(
        downloader,
        file_id=file_id,
        declared_mime_type=declared_mime_type,
        settings=settings,
    )
    if (
        downloaded.byte_size != expected_byte_size
        or downloaded.width != expected_width
        or downloaded.height != expected_height
    ):
        raise MediaRejection(MediaRejectionCode.SOURCE_CHANGED)
    return downloaded


async def download_validated_bytes(
    downloader: TelegramDownloader,
    *,
    file_id: str,
    declared_mime_type: str,
    settings: AppSettings,
) -> DownloadedMedia:
    """Download and fully validate one bounded raster into memory."""

    if declared_mime_type not in settings.document_mime_allowlist:
        raise MediaRejection(MediaRejectionCode.UNSUPPORTED_FORMAT)
    buffer = CappedBytesIO(settings.max_media_bytes)
    payload: bytes | None = None
    try:
        try:
            await downloader.download(file_id, destination=buffer)
        except MediaRejection:
            raise
        except Exception:
            raise MediaRejection(MediaRejectionCode.DOWNLOAD_FAILED) from None
        payload = buffer.getvalue()
        observed_mime_type = _observed_mime_type(payload)
        if observed_mime_type != declared_mime_type:
            raise MediaRejection(MediaRejectionCode.UNSUPPORTED_FORMAT)
        width, height = _inspect_dimensions(payload, settings=settings)
        try:
            validated = validate_image_bytes(
                payload,
                max_width=settings.max_side_px,
                max_height=settings.max_side_px,
                max_pixels=settings.max_pixels,
            )
        except ImageValidationError:
            raise MediaRejection(MediaRejectionCode.UNREADABLE) from None
        if (
            validated.width != width
            or validated.height != height
            or validated.media_type != observed_mime_type
        ):
            raise MediaRejection(MediaRejectionCode.UNREADABLE)
        return DownloadedMedia(
            data=payload,
            mime_type=observed_mime_type,
            byte_size=len(payload),
            width=width,
            height=height,
        )
    finally:
        payload = None
        buffer.close()
