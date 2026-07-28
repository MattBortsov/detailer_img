"""Hostile media boundary for custom color references."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, PngImagePlugin

from car_wrap.custom_colors.media import (
    MalwareDetectedError,
    MediaPolicy,
    MediaValidationError,
    ScanUnavailableError,
    normalize_reference,
)


class Scanner:
    def __init__(self, outcome: str = "clean") -> None:
        self.outcome = outcome
        self.calls = 0

    def scan(self, data: bytes) -> None:
        self.calls += 1
        if self.outcome == "malware":
            raise MalwareDetectedError
        if self.outcome == "unavailable":
            raise ScanUnavailableError


def image_bytes(
    image_format: str,
    *,
    size: tuple[int, int] = (80, 60),
    metadata: bool = False,
) -> bytes:
    if image_format == "HEIF":
        from pillow_heif import register_heif_opener

        register_heif_opener(thumbnails=False)
    image = Image.new("RGB", size, "#7a4b2b")
    output = BytesIO()
    options: dict[str, object] = {}
    if image_format == "PNG" and metadata:
        pnginfo = PngImagePlugin.PngInfo()
        pnginfo.add_text("canary", "must-not-survive")
        options["pnginfo"] = pnginfo
    image.save(output, format=image_format, **options)
    return output.getvalue()


@pytest.mark.parametrize(
    ("image_format", "mime"),
    (
        ("JPEG", "image/jpeg"),
        ("PNG", "image/png"),
        ("WEBP", "image/webp"),
        ("HEIF", "image/heif"),
    ),
)
def test_supported_formats_become_metadata_free_png(
    image_format: str,
    mime: str,
) -> None:
    if image_format == "HEIF":
        pytest.importorskip("pillow_heif")
    scanner = Scanner()

    result = normalize_reference(
        image_bytes(image_format, metadata=image_format == "PNG"),
        declared_mime=mime,
        scanner=scanner,
        policy=MediaPolicy(),
        isolated=False,
    )

    assert scanner.calls == 1
    assert result.media_type == "image/png"
    assert result.width == 80
    assert result.height == 60
    assert len(result.sha256) == 64
    with Image.open(BytesIO(result.data)) as canonical:
        assert canonical.format == "PNG"
        assert canonical.mode == "RGB"
        assert "canary" not in canonical.info


def test_rejects_mime_spoofing_before_persistence() -> None:
    scanner = Scanner()
    with pytest.raises(MediaValidationError, match="MIME"):
        normalize_reference(
            image_bytes("PNG"),
            declared_mime="image/jpeg",
            scanner=scanner,
            policy=MediaPolicy(),
            isolated=False,
        )
    assert scanner.calls == 0


def test_default_path_decodes_in_isolated_worker() -> None:
    result = normalize_reference(
        image_bytes("JPEG"),
        declared_mime="image/jpeg",
        scanner=Scanner(),
        policy=MediaPolicy(),
    )
    assert result.media_type == "image/png"


def test_rejects_size_pixels_animation_corruption_and_malware() -> None:
    with pytest.raises(MediaValidationError, match="byte"):
        normalize_reference(
            image_bytes("PNG"),
            declared_mime="image/png",
            scanner=Scanner(),
            policy=MediaPolicy(max_bytes=16),
            isolated=False,
        )
    with pytest.raises(MediaValidationError, match="pixel"):
        normalize_reference(
            image_bytes("PNG", size=(80, 60)),
            declared_mime="image/png",
            scanner=Scanner(),
            policy=MediaPolicy(max_pixels=100),
            isolated=False,
        )

    frames = [Image.new("RGB", (20, 20), color) for color in ("red", "blue")]
    animated = BytesIO()
    frames[0].save(
        animated,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
    )
    with pytest.raises(MediaValidationError, match="frame"):
        normalize_reference(
            animated.getvalue(),
            declared_mime="image/webp",
            scanner=Scanner(),
            policy=MediaPolicy(),
            isolated=False,
        )
    with pytest.raises(MediaValidationError):
        normalize_reference(
            b"\x89PNG\r\n\x1a\nbroken",
            declared_mime="image/png",
            scanner=Scanner(),
            policy=MediaPolicy(),
            isolated=False,
        )
    with pytest.raises(MalwareDetectedError):
        normalize_reference(
            image_bytes("PNG"),
            declared_mime="image/png",
            scanner=Scanner("malware"),
            policy=MediaPolicy(),
            isolated=False,
        )
    with pytest.raises(ScanUnavailableError):
        normalize_reference(
            image_bytes("PNG"),
            declared_mime="image/png",
            scanner=Scanner("unavailable"),
            policy=MediaPolicy(),
            isolated=False,
        )
