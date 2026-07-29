"""Telegram result normalization contracts."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from car_wrap.generation.result import (
    ResultNormalizationError,
    normalize_telegram_photo,
)


def _image(image_format: str = "PNG", *, size: tuple[int, int] = (1200, 800)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color=(25, 80, 140)).save(
        buffer,
        format=image_format,
        exif=b"private-metadata-canary" if image_format == "JPEG" else None,
    )
    return buffer.getvalue()


def test_normalizes_to_bounded_metadata_free_jpeg() -> None:
    result = normalize_telegram_photo(
        _image("PNG"),
        max_input_side=8192,
        max_input_pixels=25_000_000,
        max_output_bytes=9 * 1024 * 1024,
        max_side_sum=10_000,
    )
    assert result.data.startswith(b"\xff\xd8\xff")
    assert result.image_format == "jpeg"
    assert result.byte_count == len(result.data)
    assert "data=" not in repr(result)
    with Image.open(BytesIO(result.data)) as image:
        assert image.size == (1200, 800)
        assert not image.getexif()


def test_scales_to_telegram_side_sum() -> None:
    result = normalize_telegram_photo(
        _image("PNG", size=(7000, 4000)),
        max_input_side=8192,
        max_input_pixels=30_000_000,
        max_output_bytes=9 * 1024 * 1024,
        max_side_sum=10_000,
    )
    assert result.width + result.height <= 10_000


@pytest.mark.parametrize("payload", (b"", b"not-an-image", b"<svg/>"))
def test_rejects_invalid_or_vector_result(payload: bytes) -> None:
    with pytest.raises(ResultNormalizationError):
        normalize_telegram_photo(
            payload,
            max_input_side=8192,
            max_input_pixels=25_000_000,
            max_output_bytes=9 * 1024 * 1024,
            max_side_sum=10_000,
        )
