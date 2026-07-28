from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from car_wrap.eval.models import (
    CaseScores,
    CorpusCase,
    CorpusManifest,
    ProviderErrorCode,
    ProviderUsage,
    ScoredCase,
)
from car_wrap.eval.run_manifest import (
    EvidenceBindingError,
    GenerationCaseAttempt,
    GenerationRun,
    SafeOutcome,
    load_generation_run,
    validate_evidence_binding,
    write_generation_run,
)

_MODEL = "openai/gpt-image-2"
_PROMPT = "recolor-v1"
_START = datetime(2026, 7, 27, 12, tzinfo=UTC)


def _case(case_id: str, checksum: str) -> CorpusCase:
    return CorpusCase(
        case_id=case_id,
        source_path=f"authorized/{case_id}.png",
        source_sha256=checksum,
        vehicle_type="car" if case_id == "case-a" else "motorcycle",
        viewpoint="front" if case_id == "case-a" else "side",
        source_tone="light" if case_id == "case-a" else "dark",
        reflections=True,
        complex_background=True,
        partial_occlusion=True,
        color_id="deep-blue",
    )


def _manifest() -> CorpusManifest:
    return CorpusManifest(
        schema_version="1",
        corpus_id="locked-corpus",
        cases=(_case("case-b", "b" * 64), _case("case-a", "a" * 64)),
    )


def _output_checksum(case_id: str) -> str:
    return hashlib.sha256(f"{case_id}-output".encode()).hexdigest()


def _score(
    case_id: str,
    checksum: str,
    *,
    output_checksum: str | None = None,
) -> ScoredCase:
    return ScoredCase(
        case_id=case_id,
        source_sha256=checksum,
        output_sha256=output_checksum or _output_checksum(case_id),
        scores=CaseScores.model_validate(dict.fromkeys(CaseScores.model_fields, 4)),
    )


def _attempt(
    case_id: str,
    checksum: str,
    *,
    attempt: int = 1,
    succeeded: bool = True,
    model: str = _MODEL,
    prompt_revision: str = _PROMPT,
) -> GenerationCaseAttempt:
    started = _START + timedelta(seconds=attempt)
    return GenerationCaseAttempt(
        case_id=case_id,
        source_sha256=checksum,
        attempt=attempt,
        model=model,
        prompt_revision=prompt_revision,
        started_at=started,
        finished_at=started + timedelta(milliseconds=250),
        latency_ms=250,
        output_bytes=2048 if succeeded else None,
        output_sha256=_output_checksum(case_id) if succeeded else None,
        usage=(
            ProviderUsage(input_tokens=1, output_tokens=2, total_tokens=3)
            if succeeded
            else None
        ),
        cost=Decimal("0.04") if succeeded else None,
        peak_rss_bytes=96_000_000 if succeeded else None,
        outcome=(
            SafeOutcome(status="succeeded")
            if succeeded
            else SafeOutcome(
                status="failed",
                error_code=ProviderErrorCode.NETWORK_ERROR,
            )
        ),
    )


def _run(
    attempts: tuple[GenerationCaseAttempt, ...] | None = None,
) -> GenerationRun:
    return GenerationRun(
        schema_version="1",
        run_id="run-20260727",
        model=_MODEL,
        prompt_revision=_PROMPT,
        attempts=attempts
        or (
            _attempt("case-b", "b" * 64),
            _attempt("case-a", "a" * 64, attempt=2),
            _attempt("case-a", "a" * 64, attempt=1, succeeded=False),
        ),
    )


def _scores() -> list[ScoredCase]:
    return [_score("case-b", "b" * 64), _score("case-a", "a" * 64)]


def test_generation_run_round_trip_is_ordered_byte_stable_and_private(
    tmp_path: Path,
) -> None:
    run = _run()
    first = tmp_path / "run.json"
    second = tmp_path / "run-copy.json"

    write_generation_run(first, run)
    loaded = load_generation_run(first)
    write_generation_run(second, loaded)

    assert [item.case_id for item in loaded.attempts] == [
        "case-a",
        "case-a",
        "case-b",
    ]
    assert first.read_bytes() == second.read_bytes()
    text = first.read_text(encoding="utf-8")
    for field in (
        "source_sha256",
        "started_at",
        "finished_at",
        "latency_ms",
        "output_bytes",
        "output_sha256",
        "usage",
        "cost",
        "outcome",
    ):
        assert f'"{field}"' in text
    for forbidden in (
        "image_bytes",
        "base64",
        "data:image",
        "signed_url",
        "request_body",
        "response_body",
        "headers",
        "raw_error",
    ):
        assert forbidden not in text


@pytest.mark.parametrize(
    "unsafe_field",
    [
        "source_path",
        "output_path",
        "image_bytes",
        "base64",
        "data_url",
        "signed_url",
        "request_body",
        "response_body",
        "headers",
        "raw_error",
    ],
)
def test_attempts_reject_media_transport_and_error_fields(
    unsafe_field: str,
) -> None:
    data = _attempt("case-a", "a" * 64).model_dump()
    data[unsafe_field] = "privacy-canary"
    with pytest.raises(ValidationError):
        GenerationCaseAttempt.model_validate(data)


def test_run_rejects_version_duplicates_candidate_mismatches_and_bad_time() -> None:
    with pytest.raises(ValidationError):
        GenerationRun.model_validate({**_run().model_dump(), "schema_version": "2"})
    duplicate = _attempt("case-a", "a" * 64)
    with pytest.raises(ValidationError, match="case-attempt"):
        _run((duplicate, duplicate))
    with pytest.raises(ValidationError, match="model"):
        _run((_attempt("case-a", "a" * 64, model="other/model"),))
    with pytest.raises(ValidationError, match="prompt"):
        _run(
            (
                _attempt(
                    "case-a",
                    "a" * 64,
                    prompt_revision="other-revision",
                ),
            )
        )
    bad_time = _attempt("case-a", "a" * 64).model_dump()
    bad_time["finished_at"] = bad_time["started_at"] - timedelta(seconds=1)
    with pytest.raises(ValidationError, match="finish"):
        GenerationCaseAttempt.model_validate(bad_time)


def test_exact_binding_returns_stable_typed_evidence() -> None:
    binding = validate_evidence_binding(_manifest(), _run(), _scores())

    assert [case.case_id for case in binding.cases] == ["case-a", "case-b"]
    assert binding.model == _MODEL
    assert binding.prompt_revision == _PROMPT
    assert binding.cases[0].output_sha256 == _output_checksum("case-a")
    assert (
        binding.cases[0].selected_attempt.output_sha256
        == binding.cases[0].output_sha256
    )
    assert binding.cases[0].selected_attempt.attempt == 2
    assert [item.attempt for item in binding.cases[0].attempts] == [1, 2]


@pytest.mark.parametrize(
    ("attempts", "scores"),
    [
        (
            (_attempt("case-a", "a" * 64),),
            _scores(),
        ),
        (
            (
                _attempt("case-a", "a" * 64),
                _attempt("case-b", "b" * 64),
                _attempt("case-extra", "c" * 64),
            ),
            _scores(),
        ),
        (
            (
                _attempt("case-a", "a" * 64),
                _attempt("case-a", "a" * 64, attempt=2),
                _attempt("case-b", "b" * 64),
            ),
            _scores(),
        ),
        (
            (
                _attempt("case-a", "c" * 64),
                _attempt("case-b", "b" * 64),
            ),
            _scores(),
        ),
        (
            (
                _attempt("case-a", "a" * 64),
                _attempt("case-b", "b" * 64),
            ),
            [_score("case-a", "a" * 64)],
        ),
        (
            (
                _attempt("case-a", "a" * 64),
                _attempt("case-b", "b" * 64),
            ),
            [*_scores(), _score("case-extra", "c" * 64)],
        ),
        (
            (
                _attempt("case-a", "a" * 64),
                _attempt("case-b", "b" * 64),
            ),
            [*_scores(), _score("case-a", "a" * 64)],
        ),
        (
            (
                _attempt("case-a", "a" * 64),
                _attempt("case-b", "b" * 64),
            ),
            [_score("case-a", "c" * 64), _score("case-b", "b" * 64)],
        ),
        (
            (
                _attempt("case-a", "a" * 64),
                _attempt("case-b", "b" * 64),
            ),
            [
                _score("case-a", "a" * 64, output_checksum="c" * 64),
                _score("case-b", "b" * 64),
            ],
        ),
    ],
)
def test_binding_rejects_every_incomplete_or_mismatched_evidence_class(
    attempts: tuple[GenerationCaseAttempt, ...],
    scores: list[ScoredCase],
) -> None:
    with pytest.raises(EvidenceBindingError):
        validate_evidence_binding(_manifest(), _run(attempts), scores)
