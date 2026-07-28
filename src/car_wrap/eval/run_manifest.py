"""Versioned, metadata-only generation evidence and exact binding."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from car_wrap.eval.models import (
    CaseScores,
    ContractModel,
    CorpusManifest,
    ProviderErrorCode,
    ProviderUsage,
    ScoredCase,
)

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SAFE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_RUN_BYTES = 8 * 1024 * 1024


class GenerationRunError(ValueError):
    """Fixed-message generation-run parsing or writing failure."""

    def __init__(self) -> None:
        super().__init__("generation run validation failed")


class EvidenceBindingError(ValueError):
    """Fixed-message failure for untrusted cross-artifact evidence."""

    def __init__(self) -> None:
        super().__init__("generation evidence binding failed")


class SafeOutcome(ContractModel):
    """Allowlisted provider outcome without arbitrary error text."""

    status: Literal["succeeded", "failed"]
    error_code: ProviderErrorCode | None = None
    status_code: int | None = Field(default=None, strict=True, ge=100, le=599)

    @model_validator(mode="after")
    def validate_error_shape(self) -> SafeOutcome:
        if self.status == "succeeded" and (
            self.error_code is not None or self.status_code is not None
        ):
            raise ValueError("successful outcome cannot contain an error")
        if self.status == "failed" and self.error_code is None:
            raise ValueError("failed outcome requires a safe error code")
        return self


class GenerationCaseAttempt(ContractModel):
    """One metadata-only provider attempt bound to an authorized source."""

    case_id: str
    source_sha256: str
    attempt: int = Field(strict=True, ge=1)
    model: str
    prompt_revision: str
    started_at: datetime
    finished_at: datetime
    latency_ms: int = Field(strict=True, ge=0)
    output_bytes: int | None = Field(default=None, strict=True, ge=0)
    output_sha256: str | None = None
    usage: ProviderUsage | None = None
    cost: Decimal | None = Field(default=None, ge=Decimal("0"))
    peak_rss_bytes: int | None = Field(default=None, strict=True, ge=0)
    outcome: SafeOutcome

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("invalid case identifier")
        return value

    @field_validator("source_sha256")
    @classmethod
    def validate_checksum(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("invalid source checksum")
        return value

    @field_validator("output_sha256")
    @classmethod
    def validate_output_checksum(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.fullmatch(value):
            raise ValueError("invalid output checksum")
        return value

    @field_validator("model", "prompt_revision")
    @classmethod
    def validate_candidate_value(cls, value: str) -> str:
        if not _SAFE_MODEL.fullmatch(value):
            raise ValueError("invalid candidate value")
        return value

    @field_validator("started_at", "finished_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("attempt timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_timing_and_success(self) -> GenerationCaseAttempt:
        if self.finished_at < self.started_at:
            raise ValueError("attempt finish precedes start")
        if self.outcome.status == "succeeded" and (
            self.output_bytes is None or self.output_sha256 is None
        ):
            raise ValueError("successful attempt requires output evidence")
        if self.outcome.status == "failed" and self.output_sha256 is not None:
            raise ValueError("failed attempt cannot contain output evidence")
        return self


class GenerationRun(ContractModel):
    """Canonical candidate run containing every recorded attempt."""

    schema_version: Literal["1"]
    run_id: str
    model: str
    prompt_revision: str
    attempts: tuple[GenerationCaseAttempt, ...] = Field(min_length=1)

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("invalid run identifier")
        return value

    @field_validator("model", "prompt_revision")
    @classmethod
    def validate_candidate_value(cls, value: str) -> str:
        if not _SAFE_MODEL.fullmatch(value):
            raise ValueError("invalid run candidate value")
        return value

    @field_validator("attempts")
    @classmethod
    def order_attempts(
        cls,
        value: tuple[GenerationCaseAttempt, ...],
    ) -> tuple[GenerationCaseAttempt, ...]:
        return tuple(sorted(value, key=lambda item: (item.case_id, item.attempt)))

    @model_validator(mode="after")
    def validate_attempt_contract(self) -> GenerationRun:
        identities = [(item.case_id, item.attempt) for item in self.attempts]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate case-attempt identity")
        if any(item.model != self.model for item in self.attempts):
            raise ValueError("attempt model does not match run model")
        if any(item.prompt_revision != self.prompt_revision for item in self.attempts):
            raise ValueError("attempt prompt revision does not match run prompt")
        return self


class BoundCaseEvidence(ContractModel):
    """One exactly matched case with all attempts and its selected success."""

    case_id: str
    source_sha256: str
    output_sha256: str
    attempts: tuple[GenerationCaseAttempt, ...]
    selected_attempt: GenerationCaseAttempt
    scores: CaseScores


class EvidenceBinding(ContractModel):
    """Complete evidence safe for gate and report consumers."""

    schema_version: Literal["1"]
    corpus_id: str
    run_id: str
    model: str
    prompt_revision: str
    cases: tuple[BoundCaseEvidence, ...]


def _serialized_bytes(run: GenerationRun) -> bytes:
    payload = run.model_dump(mode="json")
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode()


def load_generation_run(path: Path) -> GenerationRun:
    """Load a bounded UTF-8 JSON run through the strict schema."""

    try:
        if path.stat().st_size > _MAX_RUN_BYTES:
            raise GenerationRunError
        return GenerationRun.model_validate_json(path.read_bytes())
    except GenerationRunError:
        raise
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise GenerationRunError from None


def write_generation_run(path: Path, run: GenerationRun) -> None:
    """Atomically write canonical UTF-8 JSON without media fields."""

    data = _serialized_bytes(run)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        raise GenerationRunError from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def validate_evidence_binding(
    manifest: CorpusManifest,
    run: GenerationRun,
    scores: Sequence[ScoredCase],
) -> EvidenceBinding:
    """Require exact case/checksum/candidate matching before consumption."""

    manifest_by_id = {case.case_id: case for case in manifest.cases}
    attempt_ids = {item.case_id for item in run.attempts}
    if attempt_ids != set(manifest_by_id):
        raise EvidenceBindingError

    attempts_by_case: dict[str, list[GenerationCaseAttempt]] = defaultdict(list)
    successes: dict[str, GenerationCaseAttempt] = {}
    for item in run.attempts:
        expected = manifest_by_id[item.case_id]
        if item.source_sha256 != expected.source_sha256:
            raise EvidenceBindingError
        attempts_by_case[item.case_id].append(item)
        if item.outcome.status == "succeeded":
            if item.case_id in successes:
                raise EvidenceBindingError
            successes[item.case_id] = item
    if set(successes) != set(manifest_by_id):
        raise EvidenceBindingError

    score_ids = [item.case_id for item in scores]
    if len(score_ids) != len(set(score_ids)) or set(score_ids) != set(manifest_by_id):
        raise EvidenceBindingError
    scores_by_id = {item.case_id: item for item in scores}
    if any(
        scores_by_id[case_id].source_sha256 != case.source_sha256
        for case_id, case in manifest_by_id.items()
    ):
        raise EvidenceBindingError
    if any(
        successes[case_id].output_sha256 != scores_by_id[case_id].output_sha256
        for case_id in manifest_by_id
    ):
        raise EvidenceBindingError

    bound_cases = tuple(
        BoundCaseEvidence(
            case_id=case_id,
            source_sha256=manifest_by_id[case_id].source_sha256,
            output_sha256=scores_by_id[case_id].output_sha256,
            attempts=tuple(attempts_by_case[case_id]),
            selected_attempt=successes[case_id],
            scores=scores_by_id[case_id].scores,
        )
        for case_id in sorted(manifest_by_id)
    )
    return EvidenceBinding(
        schema_version="1",
        corpus_id=manifest.corpus_id,
        run_id=run.run_id,
        model=run.model,
        prompt_revision=run.prompt_revision,
        cases=bound_cases,
    )
