"""Strict, immutable evaluation boundary contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "1"
_SAFE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SAFE_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_SAFE_COLOR_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9 -]{0,47}$")
_RGB_HEX_PATTERN = re.compile(r"^#[0-9A-F]{6}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_UNSAFE_TEXT_MARKERS = (
    "base64",
    "bearer ",
    "data:",
    "file:",
    "signature=",
    "x-amz-",
    "://",
)


class ContractModel(BaseModel):
    """Base class for every YAML, JSON, and report boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class VehicleType(StrEnum):
    CAR = "car"
    MOTORCYCLE = "motorcycle"


class Viewpoint(StrEnum):
    FRONT = "front"
    REAR = "rear"
    SIDE = "side"
    THREE_QUARTER = "three_quarter"


class SourceTone(StrEnum):
    LIGHT = "light"
    DARK = "dark"


class EvaluationColor(ContractModel):
    """One server-owned color intent accepted by the audited prompt builder."""

    color_id: str
    display_name: str
    rgb_hex: str

    @field_validator("color_id")
    @classmethod
    def validate_color_id(cls, value: str) -> str:
        return _validate_safe_id(value, label="color identifier")

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        if not _SAFE_COLOR_NAME_PATTERN.fullmatch(value):
            raise ValueError("invalid color display name")
        return value

    @field_validator("rgb_hex")
    @classmethod
    def validate_rgb_hex(cls, value: str) -> str:
        if not _RGB_HEX_PATTERN.fullmatch(value):
            raise ValueError("invalid color RGB value")
        return value


class CoverageCategory(StrEnum):
    """One independently required D-03 corpus category."""

    VEHICLE_CAR = "vehicle_type:car"
    VEHICLE_MOTORCYCLE = "vehicle_type:motorcycle"
    VIEWPOINT_FRONT = "viewpoint:front"
    VIEWPOINT_REAR = "viewpoint:rear"
    VIEWPOINT_SIDE = "viewpoint:side"
    VIEWPOINT_THREE_QUARTER = "viewpoint:three_quarter"
    SOURCE_TONE_LIGHT = "source_tone:light"
    SOURCE_TONE_DARK = "source_tone:dark"
    REFLECTIONS = "reflections:true"
    COMPLEX_BACKGROUND = "complex_background:true"
    PARTIAL_OCCLUSION = "partial_occlusion:true"


class ErrorCode(StrEnum):
    INVALID_MANIFEST = "invalid_manifest"
    INVALID_FIXTURE_ROOT = "invalid_fixture_root"
    INVALID_FIXTURE_PATH = "invalid_fixture_path"
    SYMLINK_FIXTURE = "symlink_fixture"
    FIXTURE_MISSING = "fixture_missing"
    FIXTURE_NOT_FILE = "fixture_not_file"
    FIXTURE_TOO_LARGE = "fixture_too_large"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    FIXTURE_READ_FAILED = "fixture_read_failed"
    INVALID_IMAGE = "invalid_image"


class ProviderErrorCode(StrEnum):
    MISSING_CREDENTIAL = "missing_credential"
    NETWORK_ERROR = "network_error"
    HTTP_ERROR = "http_error"
    INVALID_CONTENT_TYPE = "invalid_content_type"
    INVALID_JSON = "invalid_json"
    INVALID_RESPONSE_SHAPE = "invalid_response_shape"
    INVALID_BASE64 = "invalid_base64"
    OUTPUT_TOO_LARGE = "output_too_large"
    INVALID_IMAGE = "invalid_image"


_ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INVALID_MANIFEST: "manifest validation failed",
    ErrorCode.INVALID_FIXTURE_ROOT: "fixture root is invalid",
    ErrorCode.INVALID_FIXTURE_PATH: "fixture path is invalid",
    ErrorCode.SYMLINK_FIXTURE: "symlink fixtures are not allowed",
    ErrorCode.FIXTURE_MISSING: "fixture is missing",
    ErrorCode.FIXTURE_NOT_FILE: "fixture is not a regular file",
    ErrorCode.FIXTURE_TOO_LARGE: "fixture exceeds the byte limit",
    ErrorCode.CHECKSUM_MISMATCH: "fixture checksum does not match",
    ErrorCode.FIXTURE_READ_FAILED: "fixture could not be read",
    ErrorCode.INVALID_IMAGE: "fixture image is invalid",
}
_PROVIDER_ERROR_MESSAGES: dict[ProviderErrorCode, str] = {
    ProviderErrorCode.MISSING_CREDENTIAL: "provider credential is unavailable",
    ProviderErrorCode.NETWORK_ERROR: "provider request failed",
    ProviderErrorCode.HTTP_ERROR: "provider returned an unsuccessful status",
    ProviderErrorCode.INVALID_CONTENT_TYPE: "provider returned an invalid content type",
    ProviderErrorCode.INVALID_JSON: "provider returned invalid JSON",
    ProviderErrorCode.INVALID_RESPONSE_SHAPE: "provider response shape is invalid",
    ProviderErrorCode.INVALID_BASE64: "provider image encoding is invalid",
    ProviderErrorCode.OUTPUT_TOO_LARGE: "provider image exceeds the byte limit",
    ProviderErrorCode.INVALID_IMAGE: "provider image is invalid",
}


class ProviderError(Exception):
    """Fixed-message provider failure that never wraps external exception text."""

    def __init__(
        self,
        code: ProviderErrorCode,
        *,
        status_code: int | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(_PROVIDER_ERROR_MESSAGES[code])

    def __repr__(self) -> str:
        return (
            f"ProviderError(code={self.code.value!r}, status_code={self.status_code!r})"
        )


def _validate_safe_id(value: str, *, label: str) -> str:
    if not _SAFE_ID_PATTERN.fullmatch(value):
        raise ValueError(f"invalid {label}")
    return value


def _validate_safe_model_value(value: str, *, label: str) -> str:
    if not _SAFE_MODEL_PATTERN.fullmatch(value):
        raise ValueError(f"invalid {label}")
    return value


def _validate_safe_note(value: str) -> str:
    normalized = value.strip()
    lowered = normalized.lower()
    if (
        not normalized
        or len(normalized) > 256
        or normalized.startswith(("/", "~"))
        or any(marker in lowered for marker in _UNSAFE_TEXT_MARKERS)
    ):
        raise ValueError("invalid non-sensitive note")
    return normalized


class CorpusCase(ContractModel):
    """One authorized fixture and its non-sensitive coverage metadata."""

    case_id: str
    source_path: str
    source_sha256: str
    vehicle_type: VehicleType
    viewpoint: Viewpoint
    source_tone: SourceTone
    reflections: bool = Field(strict=True)
    complex_background: bool = Field(strict=True)
    partial_occlusion: bool = Field(strict=True)
    color_id: str
    notes: str | None = None

    @field_validator("case_id", "color_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        return _validate_safe_id(value, label="manifest identifier")

    @field_validator("source_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("invalid source checksum")
        return value

    @field_validator("source_path")
    @classmethod
    def validate_relative_source_path(cls, value: str) -> str:
        if not value or "\\" in value or ":" in value or value.startswith(("/", "~")):
            raise ValueError("invalid relative fixture path")
        raw_parts = value.split("/")
        if any(part in {"", ".", ".."} for part in raw_parts):
            raise ValueError("invalid relative fixture path")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("invalid relative fixture path")
        return path.as_posix()

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, value: str | None) -> str | None:
        return None if value is None else _validate_safe_note(value)


class CorpusManifest(ContractModel):
    """Versioned list of authorized evaluation fixtures."""

    schema_version: Literal["1"]
    corpus_id: str
    cases: tuple[CorpusCase, ...] = Field(min_length=1)

    @field_validator("corpus_id")
    @classmethod
    def validate_corpus_id(cls, value: str) -> str:
        return _validate_safe_id(value, label="corpus identifier")

    @model_validator(mode="after")
    def validate_unique_case_ids(self) -> CorpusManifest:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case identifiers must be unique")
        return self


class CoverageResult(ContractModel):
    """Deterministic completeness verdict across every locked D-03 category."""

    complete: bool = Field(strict=True)
    missing_categories: tuple[CoverageCategory, ...]


class CaseScores(ContractModel):
    """The eight locked D-01 human score dimensions."""

    vehicle_identity: int = Field(strict=True, ge=1, le=5)
    geometry_viewpoint: int = Field(strict=True, ge=1, le=5)
    target_coverage: int = Field(strict=True, ge=1, le=5)
    non_target_preservation: int = Field(strict=True, ge=1, le=5)
    lighting_material: int = Field(strict=True, ge=1, le=5)
    color_intent: int = Field(strict=True, ge=1, le=5)
    artifact_control: int = Field(strict=True, ge=1, le=5)
    telegram_usability: int = Field(strict=True, ge=1, le=5)


class DimensionMeans(ContractModel):
    """Run-level means that retain all eight locked dimensions."""

    vehicle_identity: Decimal
    geometry_viewpoint: Decimal
    target_coverage: Decimal
    non_target_preservation: Decimal
    lighting_material: Decimal
    color_intent: Decimal
    artifact_control: Decimal
    telegram_usability: Decimal


class GateThresholds(ContractModel):
    """Explicit D-02 release floors and aggregate pass rules."""

    schema_version: Literal["1"]
    minimum_scores: CaseScores
    minimum_mean: float = Field(strict=True, ge=1.0, le=5.0)
    minimum_case_pass_ratio: float = Field(strict=True, ge=0.0, le=1.0)
    critical_failure_floor: int = Field(strict=True, ge=1, le=5)


class ScoredCase(ContractModel):
    """One validated eight-dimension scorecard keyed by manifest case ID."""

    case_id: str
    source_sha256: str
    output_sha256: str
    scores: CaseScores

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        return _validate_safe_id(value, label="case identifier")

    @field_validator("source_sha256", "output_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("invalid evidence checksum")
        return value


class GateRule(StrEnum):
    """Stable identifiers for independently evaluated gate rules."""

    MINIMUM_VEHICLE_IDENTITY = "minimum_score:vehicle_identity"
    MINIMUM_GEOMETRY_VIEWPOINT = "minimum_score:geometry_viewpoint"
    MINIMUM_TARGET_COVERAGE = "minimum_score:target_coverage"
    MINIMUM_NON_TARGET_PRESERVATION = "minimum_score:non_target_preservation"
    MINIMUM_LIGHTING_MATERIAL = "minimum_score:lighting_material"
    MINIMUM_COLOR_INTENT = "minimum_score:color_intent"
    MINIMUM_ARTIFACT_CONTROL = "minimum_score:artifact_control"
    MINIMUM_TELEGRAM_USABILITY = "minimum_score:telegram_usability"
    MINIMUM_MEAN = "minimum_mean"
    CORPUS_COVERAGE = "corpus_coverage"
    ZERO_CRITICAL_IDENTITY = "zero_critical_failure:vehicle_identity"
    ZERO_CRITICAL_GEOMETRY = "zero_critical_failure:geometry_viewpoint"
    MINIMUM_CASE_PASS_RATIO = "minimum_case_pass_ratio"  # noqa: S105


class CaseGateResult(ContractModel):
    """Pure verdict for one scorecard under a threshold snapshot."""

    scores: CaseScores
    mean_score: Decimal
    passed: bool = Field(strict=True)
    failed_rules: tuple[GateRule, ...]


class EvaluatedCase(ContractModel):
    """Case-keyed gate result for deterministic run serialization."""

    case_id: str
    result: CaseGateResult

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        return _validate_safe_id(value, label="case identifier")


class GateResult(ContractModel):
    """Complete release verdict with thresholds and eight-dimension aggregates."""

    passed: bool = Field(strict=True)
    coverage: CoverageResult
    thresholds: GateThresholds
    cases: tuple[EvaluatedCase, ...]
    dimension_means: DimensionMeans
    case_pass_ratio: Decimal
    failed_rules: tuple[GateRule, ...]


class ProviderUsage(ContractModel):
    """Allowlisted provider usage and cost metadata."""

    input_tokens: int | None = Field(default=None, strict=True, ge=0)
    output_tokens: int | None = Field(default=None, strict=True, ge=0)
    total_tokens: int | None = Field(default=None, strict=True, ge=0)
    cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))


class ProviderMetadata(ContractModel):
    """Allowlisted provider response identifiers without bodies or URLs."""

    provider: Literal["openrouter"]
    status_code: int | None = Field(default=None, strict=True, ge=100, le=599)
    request_id: str | None = None

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_OPAQUE_ID_PATTERN.fullmatch(value):
            raise ValueError("invalid provider request identifier")
        return value


class GeneratedImageMetadata(ContractModel):
    """Allowlisted facts about one fully validated provider image."""

    model: str
    prompt_revision: str
    latency_ms: int = Field(strict=True, ge=0)
    output_bytes: int = Field(strict=True, ge=0)
    width: int = Field(strict=True, gt=0)
    height: int = Field(strict=True, gt=0)
    image_format: Literal["png", "jpeg", "webp"]
    provider: ProviderMetadata
    usage: ProviderUsage

    @field_validator("model", "prompt_revision")
    @classmethod
    def validate_safe_values(cls, value: str) -> str:
        return _validate_safe_model_value(value, label="generated image metadata")


@dataclass(frozen=True, slots=True)
class ImageGenerationRequest:
    """Ephemeral request that binds bytes to Plan 01 fixture metadata."""

    source_bytes: bytes = field(repr=False)
    source_media_type: Literal["image/png", "image/jpeg", "image/webp"]
    fixture: FixtureMetadata
    color: EvaluationColor

    def __post_init__(self) -> None:
        if (
            not self.source_bytes
            or len(self.source_bytes) != self.fixture.byte_count
            or sha256(self.source_bytes).hexdigest() != self.fixture.sha256
            or self.source_media_type != self.fixture.source_media_type
        ):
            raise ValueError("source bytes do not match validated fixture metadata")


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    """Immediate in-memory result; only metadata may cross persistence boundaries."""

    image_bytes: bytes = field(repr=False)
    metadata: GeneratedImageMetadata


class RunMetadata(ContractModel):
    """Metadata-only record for one provider evaluation attempt."""

    schema_version: Literal["1"]
    run_id: str
    model: str
    prompt_revision: str
    started_at: datetime
    latency_ms: int = Field(strict=True, ge=0)
    output_bytes: int = Field(strict=True, ge=0)
    peak_rss_bytes: int | None = Field(default=None, strict=True, ge=0)
    provider: ProviderMetadata
    usage: ProviderUsage

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return _validate_safe_id(value, label="run identifier")

    @field_validator("model", "prompt_revision")
    @classmethod
    def validate_safe_identifiers(cls, value: str) -> str:
        return _validate_safe_model_value(value, label="run metadata value")

    @field_validator("started_at")
    @classmethod
    def validate_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("run timestamp must be timezone-aware")
        return value


class FixtureMetadata(ContractModel):
    """Validated fixture facts safe to retain outside the read boundary."""

    case_id: str
    byte_count: int = Field(strict=True, ge=0)
    sha256: str
    source_media_type: Literal["image/png", "image/jpeg", "image/webp"]

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        return _validate_safe_id(value, label="case identifier")

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("invalid fixture checksum")
        return value


class SafeError(ContractModel):
    """Fixed-message error contract with no arbitrary exception text."""

    code: ErrorCode

    @computed_field
    def message(self) -> str:
        return _ERROR_MESSAGES[self.code]
