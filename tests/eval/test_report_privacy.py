from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from car_wrap.eval.gate import evaluate_gate
from car_wrap.eval.manifest import load_manifest
from car_wrap.eval.models import (
    CaseScores,
    GateRule,
    GateThresholds,
    ProviderErrorCode,
    ProviderUsage,
    ScoredCase,
)
from car_wrap.eval.report import ReportError, build_report, write_report
from car_wrap.eval.run_manifest import (
    GenerationCaseAttempt,
    GenerationRun,
    SafeOutcome,
    validate_evidence_binding,
)

_START = datetime(2026, 7, 27, 12, tzinfo=UTC)


def _output_checksum(case_id: str) -> str:
    return hashlib.sha256(f"{case_id}-output".encode()).hexdigest()


def _inputs(*, reverse: bool = False) -> tuple[object, object, object, object]:
    original = load_manifest(Path("eval/corpus.example.yaml"))
    manifest = (
        original.model_copy(update={"cases": tuple(reversed(original.cases))})
        if reverse
        else original
    )
    scores = [
        ScoredCase(
            case_id=case.case_id,
            source_sha256=case.source_sha256,
            output_sha256=_output_checksum(case.case_id),
            scores=CaseScores.model_validate(dict.fromkeys(CaseScores.model_fields, 4)),
        )
        for case in manifest.cases
    ]
    attempts: list[GenerationCaseAttempt] = []
    ordered_ids = sorted(case.case_id for case in manifest.cases)
    for case in manifest.cases:
        index = ordered_ids.index(case.case_id)
        started = _START + timedelta(seconds=index)
        if case.case_id == ordered_ids[0]:
            attempts.append(
                GenerationCaseAttempt(
                    case_id=case.case_id,
                    source_sha256=case.source_sha256,
                    attempt=1,
                    model="openai/gpt-image-2",
                    prompt_revision="recolor-v1",
                    started_at=started,
                    finished_at=started + timedelta(milliseconds=100),
                    latency_ms=100,
                    output_bytes=None,
                    usage=None,
                    cost=None,
                    outcome=SafeOutcome(
                        status="failed",
                        error_code=ProviderErrorCode.NETWORK_ERROR,
                    ),
                )
            )
        attempts.append(
            GenerationCaseAttempt(
                case_id=case.case_id,
                source_sha256=case.source_sha256,
                attempt=2 if case.case_id == ordered_ids[0] else 1,
                model="openai/gpt-image-2",
                prompt_revision="recolor-v1",
                started_at=started + timedelta(seconds=1),
                finished_at=started + timedelta(seconds=2),
                latency_ms=1000,
                output_bytes=4096,
                output_sha256=_output_checksum(case.case_id),
                usage=ProviderUsage(
                    input_tokens=1,
                    output_tokens=2,
                    total_tokens=3,
                ),
                cost=Decimal("0.05"),
                peak_rss_bytes=96_000_000,
                outcome=SafeOutcome(status="succeeded"),
            )
        )
    if reverse:
        attempts.reverse()
        scores.reverse()
    run = GenerationRun(
        schema_version="1",
        run_id="locked-run",
        model="openai/gpt-image-2",
        prompt_revision="recolor-v1",
        attempts=tuple(attempts),
    )
    thresholds = GateThresholds.model_validate(
        yaml.safe_load(Path("eval/thresholds.yaml").read_text(encoding="utf-8"))
    )
    binding = validate_evidence_binding(manifest, run, scores)
    gate = evaluate_gate(manifest, binding, thresholds)
    return manifest, binding, thresholds, gate


def test_report_preserves_all_quality_and_generation_audit_fields() -> None:
    manifest, binding, thresholds, gate = _inputs()
    report = build_report(manifest, binding, thresholds, gate)

    assert report.schema_version == "1"
    assert report.verdict == "pass"
    assert report.model == "openai/gpt-image-2"
    assert report.prompt_revision == "recolor-v1"
    assert report.thresholds == gate.thresholds
    assert tuple(type(report.dimension_means).model_fields) == tuple(
        CaseScores.model_fields
    )
    assert tuple(type(report.cases[0].scores).model_fields) == tuple(
        CaseScores.model_fields
    )
    assert report.case_count == len(binding.cases)
    assert report.passing_case_count == len(binding.cases)
    assert report.cases[0].attempts
    assert report.cases[0].output_sha256 == _output_checksum(report.cases[0].case_id)
    assert any(
        attempt.peak_rss_bytes == 96_000_000
        for case in report.cases
        for attempt in case.attempts
    )


def test_equivalent_shuffled_evidence_writes_byte_identical_reports(
    tmp_path: Path,
) -> None:
    first_manifest, first_binding, first_thresholds, first_gate = _inputs()
    second_manifest, second_binding, second_thresholds, second_gate = _inputs(
        reverse=True
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    write_report(
        first,
        build_report(first_manifest, first_binding, first_thresholds, first_gate),
    )
    write_report(
        second,
        build_report(second_manifest, second_binding, second_thresholds, second_gate),
    )

    assert first.read_bytes() == second.read_bytes()


def test_report_rejects_gate_not_created_from_bound_scores() -> None:
    manifest, binding, thresholds, gate = _inputs()
    altered_case = gate.cases[0].model_copy(
        update={
            "result": gate.cases[0].result.model_copy(
                update={
                    "scores": gate.cases[0].result.scores.model_copy(
                        update={"color_intent": 3}
                    )
                }
            )
        }
    )
    altered_gate = gate.model_copy(update={"cases": (altered_case, *gate.cases[1:])})

    with pytest.raises(ReportError, match="report evidence validation failed"):
        build_report(manifest, binding, thresholds, altered_gate)


def test_report_rejects_every_inconsistent_derived_gate_field() -> None:
    manifest, binding, thresholds, gate = _inputs()
    first = gate.cases[0]
    mutations = {
        "passed": gate.model_copy(update={"passed": False}),
        "coverage": gate.model_copy(
            update={"coverage": gate.coverage.model_copy(update={"complete": False})}
        ),
        "thresholds": gate.model_copy(
            update={
                "thresholds": gate.thresholds.model_copy(update={"minimum_mean": 3.5})
            }
        ),
        "case_mean": gate.model_copy(
            update={
                "cases": (
                    first.model_copy(
                        update={
                            "result": first.result.model_copy(
                                update={"mean_score": Decimal("3.5")}
                            )
                        }
                    ),
                    *gate.cases[1:],
                )
            }
        ),
        "case_passed": gate.model_copy(
            update={
                "cases": (
                    first.model_copy(
                        update={
                            "result": first.result.model_copy(update={"passed": False})
                        }
                    ),
                    *gate.cases[1:],
                )
            }
        ),
        "case_failed_rules": gate.model_copy(
            update={
                "cases": (
                    first.model_copy(
                        update={
                            "result": first.result.model_copy(
                                update={"failed_rules": (GateRule.MINIMUM_MEAN,)}
                            )
                        }
                    ),
                    *gate.cases[1:],
                )
            }
        ),
        "dimension_means": gate.model_copy(
            update={
                "dimension_means": gate.dimension_means.model_copy(
                    update={"color_intent": Decimal("3.5")}
                )
            }
        ),
        "case_pass_ratio": gate.model_copy(update={"case_pass_ratio": Decimal("0.5")}),
        "failed_rules": gate.model_copy(
            update={"failed_rules": (GateRule.CORPUS_COVERAGE,)}
        ),
    }

    for altered in mutations.values():
        with pytest.raises(ReportError, match="report evidence validation failed"):
            build_report(manifest, binding, thresholds, altered)


def test_report_and_safe_failures_exclude_every_privacy_canary(
    tmp_path: Path,
) -> None:
    manifest, binding, thresholds, gate = _inputs()
    destination = tmp_path / "report.json"
    write_report(destination, build_report(manifest, binding, thresholds, gate))
    serialized = destination.read_text(encoding="utf-8")

    for canary in (
        "Bearer privacy-canary-token",
        "cHJpdmFjeS1jYW5hcnk=",
        "data:image/png;base64,privacy-canary",
        "https://media.example/output?X-Amz-Signature=privacy-canary",
        "/private/authorized/fixture.png",
        "raw-provider-error-canary",
        "request_body",
        "response_body",
        "headers",
        "image_bytes",
    ):
        assert canary not in serialized
