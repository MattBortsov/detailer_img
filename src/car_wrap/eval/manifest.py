"""Secure parsing and validation for authorized evaluation fixtures."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import ValidationError

from car_wrap.eval.image_validation import (
    ImageValidationError,
    validate_image_bytes,
)
from car_wrap.eval.models import (
    CorpusCase,
    CorpusManifest,
    ErrorCode,
    FixtureMetadata,
    SafeError,
)

_HASH_CHUNK_BYTES = 64 * 1024
_MAX_MANIFEST_BYTES = 1024 * 1024


class ManifestLoadError(ValueError):
    """Manifest failure that exposes only a fixed, non-sensitive message."""

    def __init__(self) -> None:
        self.safe_error = SafeError(code=ErrorCode.INVALID_MANIFEST)
        super().__init__(self.safe_error.message)


class FixtureValidationError(ValueError):
    """Fixture failure that exposes only a fixed, non-sensitive message."""

    def __init__(self, code: ErrorCode) -> None:
        self.safe_error = SafeError(code=code)
        super().__init__(self.safe_error.message)


def load_manifest(path: Path) -> CorpusManifest:
    """Load a bounded YAML manifest through strict Pydantic validation."""

    try:
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise ManifestLoadError
        with path.open("r", encoding="utf-8") as stream:
            raw: Any = yaml.safe_load(stream)
        return CorpusManifest.model_validate(raw)
    except ManifestLoadError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError, TypeError):
        raise ManifestLoadError from None


def _resolve_fixture(root: Path, relative_path: str) -> Path:
    if root.is_symlink():
        raise FixtureValidationError(ErrorCode.INVALID_FIXTURE_ROOT)
    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        raise FixtureValidationError(ErrorCode.INVALID_FIXTURE_ROOT) from None
    if not resolved_root.is_dir():
        raise FixtureValidationError(ErrorCode.INVALID_FIXTURE_ROOT)

    if (
        not relative_path
        or "\\" in relative_path
        or ":" in relative_path
        or relative_path.startswith(("/", "~"))
    ):
        raise FixtureValidationError(ErrorCode.INVALID_FIXTURE_PATH)
    raw_parts = relative_path.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise FixtureValidationError(ErrorCode.INVALID_FIXTURE_PATH)
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise FixtureValidationError(ErrorCode.INVALID_FIXTURE_PATH)

    cursor = resolved_root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise FixtureValidationError(ErrorCode.SYMLINK_FIXTURE)

    try:
        resolved_fixture = cursor.resolve(strict=True)
    except FileNotFoundError:
        raise FixtureValidationError(ErrorCode.FIXTURE_MISSING) from None
    except OSError:
        raise FixtureValidationError(ErrorCode.FIXTURE_READ_FAILED) from None

    try:
        resolved_fixture.relative_to(resolved_root)
    except ValueError:
        raise FixtureValidationError(ErrorCode.INVALID_FIXTURE_PATH) from None
    if not resolved_fixture.is_file():
        raise FixtureValidationError(ErrorCode.FIXTURE_NOT_FILE)
    return resolved_fixture


def validate_fixture(
    root: Path,
    case: CorpusCase,
    *,
    max_bytes: int,
    max_width: int = 8192,
    max_height: int = 8192,
    max_pixels: int = 25_000_000,
) -> FixtureMetadata:
    """Hash, verify, and fully decode one fixture within explicit limits."""

    if max_bytes <= 0:
        raise FixtureValidationError(ErrorCode.FIXTURE_TOO_LARGE)
    fixture = _resolve_fixture(root, case.source_path)

    try:
        if fixture.stat().st_size > max_bytes:
            raise FixtureValidationError(ErrorCode.FIXTURE_TOO_LARGE)
        digest = hashlib.sha256()
        byte_count = 0
        content = bytearray()
        with fixture.open("rb") as stream:
            while chunk := stream.read(_HASH_CHUNK_BYTES):
                byte_count += len(chunk)
                if byte_count > max_bytes:
                    raise FixtureValidationError(ErrorCode.FIXTURE_TOO_LARGE)
                digest.update(chunk)
                content.extend(chunk)
    except FixtureValidationError:
        raise
    except OSError:
        raise FixtureValidationError(ErrorCode.FIXTURE_READ_FAILED) from None

    checksum = digest.hexdigest()
    if checksum != case.source_sha256:
        raise FixtureValidationError(ErrorCode.CHECKSUM_MISMATCH)
    try:
        image = validate_image_bytes(
            bytes(content),
            max_width=max_width,
            max_height=max_height,
            max_pixels=max_pixels,
        )
    except ImageValidationError:
        raise FixtureValidationError(ErrorCode.INVALID_IMAGE) from None
    return FixtureMetadata(
        case_id=case.case_id,
        byte_count=byte_count,
        sha256=checksum,
        source_media_type=image.media_type,
    )
