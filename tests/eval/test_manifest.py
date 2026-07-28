from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import BinaryIO

import pytest
from PIL import Image, PngImagePlugin
from pydantic import ValidationError

from car_wrap.eval.manifest import (
    FixtureValidationError,
    ManifestLoadError,
    load_manifest,
    validate_fixture,
)
from car_wrap.eval.models import CorpusCase


def _case(
    *,
    source_path: str = "cars/source.jpg",
    source_sha256: str = "a" * 64,
) -> CorpusCase:
    return CorpusCase(
        case_id="case-one",
        source_path=source_path,
        source_sha256=source_sha256,
        vehicle_type="car",
        viewpoint="front",
        source_tone="light",
        reflections=True,
        complex_background=False,
        partial_occlusion=False,
        color_id="deep-blue",
    )


def _png(
    *,
    width: int = 2,
    height: int = 2,
    padding_bytes: int = 0,
    animated: bool = False,
) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (width, height), color=(12, 58, 102))
    kwargs: dict[str, object] = {}
    if padding_bytes:
        info = PngImagePlugin.PngInfo()
        info.add_text("padding", "x" * padding_bytes, zip=False)
        kwargs["pnginfo"] = info
    if animated:
        kwargs.update(
            save_all=True,
            append_images=[Image.new("RGB", (width, height), color=(102, 58, 12))],
            duration=100,
            loop=0,
        )
    image.save(buffer, format="PNG", **kwargs)
    return buffer.getvalue()


def test_load_manifest_strictly_validates_yaml(tmp_path: Path) -> None:
    manifest_path = tmp_path / "corpus.yaml"
    manifest_path.write_text(
        """
schema_version: "1"
corpus_id: locked-v1
cases:
  - case_id: case-one
    source_path: cars/source.jpg
    source_sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    vehicle_type: car
    viewpoint: front
    source_tone: light
    reflections: true
    complex_background: false
    partial_occlusion: false
    color_id: deep-blue
""".strip(),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert manifest.corpus_id == "locked-v1"
    assert manifest.cases[0].case_id == "case-one"

    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8") + "\nunknown: rejected\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestLoadError, match="manifest validation failed"):
        load_manifest(manifest_path)


def test_validate_fixture_hashes_with_bounded_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "cars" / "source.jpg"
    fixture.parent.mkdir()
    media = _png(padding_bytes=150_000)
    fixture.write_bytes(media)
    digest = hashlib.sha256(media).hexdigest()
    read_sizes: list[int] = []
    original_open = Path.open

    class _ReadGuard:
        def __init__(self, stream: BinaryIO) -> None:
            self._stream = stream

        def __enter__(self) -> _ReadGuard:
            return self

        def __exit__(self, *args: object) -> None:
            self._stream.close()

        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= 64 * 1024
            read_sizes.append(size)
            return self._stream.read(size)

    def guarded_open(
        path: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        stream = original_open(path, mode, *args, **kwargs)
        if path == fixture and mode == "rb":
            return _ReadGuard(stream)  # type: ignore[arg-type]
        return stream

    monkeypatch.setattr(Path, "open", guarded_open)
    metadata = validate_fixture(
        tmp_path,
        _case(source_sha256=digest),
        max_bytes=len(media),
    )

    assert metadata.case_id == "case-one"
    assert metadata.byte_count == len(media)
    assert metadata.sha256 == digest
    assert metadata.source_media_type == "image/png"
    assert len(read_sizes) > 1
    assert "source_path" not in metadata.model_dump()


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/private/authorized/source.jpg",
        "../outside.jpg",
        "cars/../../outside.jpg",
        "https://media.example.test/source.jpg",
    ],
)
def test_validate_fixture_rejects_absolute_and_traversing_paths(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    case = _case().model_copy(update={"source_path": unsafe_path})

    with pytest.raises(FixtureValidationError, match="fixture path is invalid"):
        validate_fixture(tmp_path, case, max_bytes=1024)


def test_validate_fixture_rejects_every_symlink_component(tmp_path: Path) -> None:
    outside = tmp_path.parent / "privacy-canary-outside.jpg"
    outside.write_bytes(b"privacy-canary-media")
    symlink = tmp_path / "cars"
    symlink.symlink_to(outside.parent, target_is_directory=True)
    case = _case(source_path=f"{symlink.name}/{outside.name}")

    try:
        with pytest.raises(
            FixtureValidationError,
            match="symlink fixtures are not allowed",
        ) as raised:
            validate_fixture(tmp_path, case, max_bytes=1024)
        assert str(outside) not in str(raised.value)
        assert "privacy-canary-media" not in str(raised.value)
    finally:
        outside.unlink(missing_ok=True)


def test_validate_fixture_rejects_missing_oversized_and_mismatched_files(
    tmp_path: Path,
) -> None:
    with pytest.raises(FixtureValidationError, match="fixture is missing"):
        validate_fixture(tmp_path, _case(), max_bytes=1024)

    fixture = tmp_path / "cars" / "source.jpg"
    fixture.parent.mkdir()
    fixture.write_bytes(b"privacy-canary-media")

    with pytest.raises(
        FixtureValidationError,
        match="fixture exceeds the byte limit",
    ) as oversized:
        validate_fixture(tmp_path, _case(), max_bytes=4)
    with pytest.raises(
        FixtureValidationError,
        match="fixture checksum does not match",
    ) as mismatch:
        validate_fixture(tmp_path, _case(), max_bytes=1024)

    absolute_path = str(fixture.resolve())
    for error in (oversized.value, mismatch.value):
        assert absolute_path not in str(error)
        assert "privacy-canary-media" not in str(error)


@pytest.mark.parametrize(
    ("media", "limits"),
    [
        (b"\x89PNG\r\n\x1a\ntruncated", {}),
        (_png(width=3), {"max_width": 2}),
        (_png(width=3, height=3), {"max_pixels": 8}),
        (_png(animated=True), {}),
    ],
)
def test_validate_fixture_fully_decodes_and_enforces_image_limits(
    tmp_path: Path,
    media: bytes,
    limits: dict[str, int],
) -> None:
    fixture = tmp_path / "cars" / "source.jpg"
    fixture.parent.mkdir()
    fixture.write_bytes(media)
    case = _case(source_sha256=hashlib.sha256(media).hexdigest())

    with pytest.raises(FixtureValidationError, match="fixture image is invalid"):
        validate_fixture(tmp_path, case, max_bytes=len(media), **limits)


def test_example_manifest_is_schema_valid_and_covers_locked_axes() -> None:
    manifest = load_manifest(Path("eval/corpus.example.yaml"))

    assert {case.vehicle_type.value for case in manifest.cases} == {
        "car",
        "motorcycle",
    }
    assert {case.viewpoint.value for case in manifest.cases} == {
        "front",
        "rear",
        "side",
        "three_quarter",
    }
    assert {case.source_tone.value for case in manifest.cases} == {
        "light",
        "dark",
    }
    assert any(case.reflections for case in manifest.cases)
    assert any(case.complex_background for case in manifest.cases)
    assert any(case.partial_occlusion for case in manifest.cases)


def test_manifest_models_reject_incomplete_category_values() -> None:
    data = _case().model_dump()
    data.pop("viewpoint")

    with pytest.raises(ValidationError):
        CorpusCase.model_validate(data)
