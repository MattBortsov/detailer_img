"""Bounded, metadata-free in-memory Telegram photo normalization."""

from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass, field
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from car_wrap.eval.image_validation import ImageValidationError, validate_image_bytes

_QUALITY_LADDER = (92, 86, 80, 74)
_MAX_DOWNSCALE_ROUNDS = 4


class ResultNormalizationError(ValueError):
    """A provider result cannot be sent safely as a Telegram photo."""


@dataclass(frozen=True, slots=True)
class TelegramPhoto:
    data: bytes = field(repr=False)
    width: int
    height: int
    byte_count: int
    image_format: str
    sha256: str


def normalize_telegram_photo(
    data: bytes,
    *,
    max_input_side: int,
    max_input_pixels: int,
    max_output_bytes: int,
    max_side_sum: int,
) -> TelegramPhoto:
    """Fully decode and re-render one result without filesystem access."""

    try:
        validate_image_bytes(
            data,
            max_width=max_input_side,
            max_height=max_input_side,
            max_pixels=max_input_pixels,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                oriented = ImageOps.exif_transpose(source)
                oriented.load()
                image = oriented.convert("RGB")
    except (
        ImageValidationError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        raise ResultNormalizationError from None

    width, height = image.size
    if min(width, height) <= 0 or max(width, height) / min(width, height) > 20:
        image.close()
        raise ResultNormalizationError
    if width + height > max_side_sum:
        scale = max_side_sum / (width + height)
        image = _resized(image, scale)

    encoded: bytes | None = None
    try:
        for round_number in range(_MAX_DOWNSCALE_ROUNDS + 1):
            for quality in _QUALITY_LADDER:
                buffer = BytesIO()
                try:
                    image.save(
                        buffer,
                        format="JPEG",
                        quality=quality,
                        optimize=True,
                        progressive=True,
                    )
                    candidate = buffer.getvalue()
                finally:
                    buffer.close()
                if len(candidate) <= max_output_bytes:
                    encoded = candidate
                    break
            if encoded is not None:
                break
            if round_number < _MAX_DOWNSCALE_ROUNDS:
                image = _resized(image, 0.85)
        if encoded is None:
            raise ResultNormalizationError
        width, height = image.size
        return TelegramPhoto(
            data=encoded,
            width=width,
            height=height,
            byte_count=len(encoded),
            image_format="jpeg",
            sha256=hashlib.sha256(encoded).hexdigest(),
        )
    finally:
        encoded = None
        image.close()


def _resized(image: Image.Image, scale: float) -> Image.Image:
    width, height = image.size
    resized = image.resize(
        (max(1, int(width * scale)), max(1, int(height * scale))),
        Image.Resampling.LANCZOS,
    )
    image.close()
    return resized
