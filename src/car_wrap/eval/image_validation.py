"""Shared, bounded in-memory image decoding for evaluation boundaries."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from PIL import Image, UnidentifiedImageError

ImageFormat = Literal["png", "jpeg", "webp"]
ImageMediaType = Literal["image/png", "image/jpeg", "image/webp"]

_SIGNATURES: tuple[
    tuple[ImageFormat, ImageMediaType, bytes, bytes | None],
    ...,
] = (
    ("png", "image/png", b"\x89PNG\r\n\x1a\n", None),
    ("jpeg", "image/jpeg", b"\xff\xd8\xff", None),
    ("webp", "image/webp", b"RIFF", b"WEBP"),
)


class ImageValidationError(ValueError):
    """An image failed the allowlisted decode and resource constraints."""


@dataclass(frozen=True, slots=True)
class ValidatedImage:
    """Safe facts from a completely decoded, supported single-frame image."""

    width: int
    height: int
    image_format: ImageFormat
    media_type: ImageMediaType


def _signature(data: bytes) -> tuple[ImageFormat, ImageMediaType]:
    for image_format, media_type, prefix, secondary in _SIGNATURES:
        if data.startswith(prefix) and (secondary is None or data[8:12] == secondary):
            return image_format, media_type
    raise ImageValidationError


def validate_image_bytes(
    data: bytes,
    *,
    max_width: int,
    max_height: int,
    max_pixels: int,
) -> ValidatedImage:
    """Verify and fully decode one supported image without filesystem writes."""

    if not data or max_width <= 0 or max_height <= 0 or max_pixels <= 0:
        raise ImageValidationError
    expected_format, media_type = _signature(data)
    pillow_formats = (expected_format.upper(),)

    def inspect(image: Image.Image) -> tuple[int, int]:
        width, height = image.size
        if (
            width <= 0
            or height <= 0
            or width > max_width
            or height > max_height
            or width * height > max_pixels
            or getattr(image, "n_frames", 1) != 1
            or image.format is None
            or image.format.lower() != expected_format
        ):
            raise ImageValidationError
        return width, height

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=pillow_formats) as image:
                width, height = inspect(image)
                image.verify()
            with Image.open(BytesIO(data), formats=pillow_formats) as image:
                if inspect(image) != (width, height):
                    raise ImageValidationError
                image.load()
    except ImageValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        raise ImageValidationError from None

    return ValidatedImage(
        width=width,
        height=height,
        image_format=expected_format,
        media_type=media_type,
    )
