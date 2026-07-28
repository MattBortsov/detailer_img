from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from car_wrap.eval.gate import GateInputError, evaluate_case, evaluate_gate
from car_wrap.eval.manifest import load_manifest
from car_wrap.eval.models import (
    CaseScores,
    CorpusManifest,
    GateThresholds,
    ProviderUsage,
    ScoredCase,
)
from car_wrap.eval.run_manifest import (
    EvidenceBinding,
    GenerationCaseAttempt,
    GenerationRun,
    SafeOutcome,
    validate_evidence_binding,
)

DIMENSIONS = tuple(CaseScores.model_fields)


def _thresholds() -> GateThresholds:
    return GateThresholds.model_validate(
        yaml.safe_load(Path("eval/thresholds.yaml").read_text(encoding="utf-8"))
    )


def _scores(value: int = 4, **updates: int) -> CaseScores:
    return CaseScores.model_validate({**dict.fromkeys(DIMENSIONS, value), **updates})


def _manifest() -> CorpusManifest:
    return load_manifest(Path("eval/corpus.example.yaml"))


def _scored_cases(
    manifest: CorpusManifest,
    *,
    overrides: dict[str, CaseScores] | None = None,
) -> list[ScoredCase]:
    replacements = overrides or {}
    return [
        ScoredCase(
            case_id=case.case_id,
            source_sha256=case.source_sha256,
            output_sha256=hashlib.sha256(f"{case.case_id}-output".encode()).hexdigest(),
            scores=replacements.get(case.case_id, _scores()),
        )
        for case in manifest.cases
    ]


def _binding(
    manifest: CorpusManifest,
    scored: list[ScoredCase] | None = None,
) -> EvidenceBinding:
    selected = scored or _scored_cases(manifest)
    started = datetime(2026, 7, 27, tzinfo=UTC)
    run = GenerationRun(
        schema_version="1",
        run_id="gate-test",
        model="openai/gpt-image-2",
        prompt_revision="recolor-v1",
        attempts=tuple(
            GenerationCaseAttempt(
                case_id=case.case_id,
                source_sha256=case.source_sha256,
                attempt=1,
                model="openai/gpt-image-2",
                prompt_revision="recolor-v1",
                started_at=started,
                finished_at=started + timedelta(milliseconds=1),
                latency_ms=1,
                output_bytes=128,
                output_sha256=hashlib.sha256(
                    f"{case.case_id}-output".encode()
                ).hexdigest(),
                usage=ProviderUsage(),
                outcome=SafeOutcome(status="succeeded"),
            )
            for case in manifest.cases
        ),
    )
    return validate_evidence_binding(manifest, run, selected)


def test_threshold_file_is_visible_strict_and_conservative() -> None:
    thresholds = _thresholds()

    assert thresholds.schema_version == "1"
    assert thresholds.minimum_scores == _scores(
        3,
        vehicle_identity=4,
        geometry_viewpoint=4,
        non_target_preservation=4,
    )
    assert thresholds.minimum_mean == 4.0
    assert thresholds.minimum_case_pass_ratio == 0.8
    assert thresholds.critical_failure_floor == 3


@pytest.mark.parametrize("dimension", DIMENSIONS)
def test_each_dimension_floor_is_independently_enforced(dimension: str) -> None:
    thresholds = _thresholds()
    minimum = getattr(thresholds.minimum_scores, dimension)
    passing = _scores(5, **{dimension: minimum})
    failing = _scores(5, **{dimension: minimum - 1})

    assert evaluate_case(passing, thresholds).passed is True
    result = evaluate_case(failing, thresholds)

    assert result.passed is False
    assert tuple(rule.value for rule in result.failed_rules) == (
        f"minimum_score:{dimension}",
    )


def test_mean_floor_equality_passes_and_value_below_fails() -> None:
    thresholds = _thresholds()

    assert evaluate_case(_scores(4), thresholds).passed is True
    result = evaluate_case(
        _scores(4, target_coverage=3),
        thresholds,
    )

    assert result.passed is False
    assert tuple(rule.value for rule in result.failed_rules) == ("minimum_mean",)


def test_case_failures_accumulate_in_stable_rule_order() -> None:
    result = evaluate_case(_scores(1), _thresholds())

    assert tuple(rule.value for rule in result.failed_rules) == (
        "minimum_score:vehicle_identity",
        "minimum_score:geometry_viewpoint",
        "minimum_score:target_coverage",
        "minimum_score:non_target_preservation",
        "minimum_score:lighting_material",
        "minimum_score:color_intent",
        "minimum_score:artifact_control",
        "minimum_score:telegram_usability",
        "minimum_mean",
    )


@pytest.mark.parametrize("invalid", [0, 6])
def test_scores_outside_one_to_five_are_invalid_input(invalid: int) -> None:
    data = dict.fromkeys(DIMENSIONS, 4)
    data["vehicle_identity"] = invalid

    with pytest.raises(ValidationError):
        CaseScores.model_validate(data)


def test_complete_passing_run_preserves_all_eight_dimension_aggregates() -> None:
    manifest = _manifest()
    result = evaluate_gate(manifest, _binding(manifest), _thresholds())

    assert result.passed is True
    assert result.failed_rules == ()
    assert result.case_pass_ratio == 1
    assert tuple(type(result.dimension_means).model_fields) == DIMENSIONS
    assert all(value == 4 for value in result.dimension_means.model_dump().values())
    assert result.thresholds == _thresholds()


def test_incomplete_coverage_is_a_valid_failing_verdict() -> None:
    manifest = CorpusManifest(
        schema_version="1",
        corpus_id="incomplete",
        cases=(_manifest().cases[0],),
    )

    result = evaluate_gate(manifest, _binding(manifest), _thresholds())

    assert result.passed is False
    assert result.coverage.complete is False
    assert tuple(rule.value for rule in result.failed_rules) == ("corpus_coverage",)


def test_case_pass_ratio_equality_passes_and_below_boundary_fails() -> None:
    manifest = _manifest()
    five_case_manifest = CorpusManifest(
        schema_version="1",
        corpus_id="ratio-boundary",
        cases=manifest.cases[:5],
    )
    one_failure = {five_case_manifest.cases[0].case_id: _scores(4, target_coverage=2)}
    boundary = evaluate_gate(
        five_case_manifest,
        _binding(
            five_case_manifest,
            _scored_cases(five_case_manifest, overrides=one_failure),
        ),
        _thresholds(),
    )
    assert boundary.case_pass_ratio == Decimal("0.8")
    assert boundary.passed is True

    two_failures = {
        manifest.cases[0].case_id: _scores(4, target_coverage=2),
        manifest.cases[1].case_id: _scores(4, color_intent=2),
    }
    below = evaluate_gate(
        manifest,
        _binding(manifest, _scored_cases(manifest, overrides=two_failures)),
        _thresholds(),
    )
    assert below.case_pass_ratio == Decimal("0.75")
    assert tuple(rule.value for rule in below.failed_rules) == (
        "minimum_case_pass_ratio",
    )


@pytest.mark.parametrize(
    ("updates", "rule"),
    [
        ({"vehicle_identity": 2}, "zero_critical_failure:vehicle_identity"),
        (
            {"geometry_viewpoint": 2},
            "zero_critical_failure:geometry_viewpoint",
        ),
    ],
)
def test_zero_critical_failure_rules_cannot_be_masked_by_ratio(
    updates: dict[str, int],
    rule: str,
) -> None:
    manifest = _manifest()
    overrides = {manifest.cases[0].case_id: _scores(4, **updates)}

    result = evaluate_gate(
        manifest,
        _binding(manifest, _scored_cases(manifest, overrides=overrides)),
        _thresholds(),
    )

    assert result.case_pass_ratio > 0.8
    assert result.passed is False
    assert rule in tuple(failed.value for failed in result.failed_rules)


def test_combined_gate_failures_accumulate_in_stable_order() -> None:
    manifest = CorpusManifest(
        schema_version="1",
        corpus_id="combined",
        cases=(_manifest().cases[0],),
    )
    scored = [
        ScoredCase(
            case_id=manifest.cases[0].case_id,
            source_sha256=manifest.cases[0].source_sha256,
            output_sha256=hashlib.sha256(
                f"{manifest.cases[0].case_id}-output".encode()
            ).hexdigest(),
            scores=_scores(
                1,
                vehicle_identity=1,
                geometry_viewpoint=1,
            ),
        )
    ]

    result = evaluate_gate(manifest, _binding(manifest, scored), _thresholds())

    assert tuple(rule.value for rule in result.failed_rules) == (
        "corpus_coverage",
        "zero_critical_failure:vehicle_identity",
        "zero_critical_failure:geometry_viewpoint",
        "minimum_case_pass_ratio",
    )


def test_gate_rejects_loose_or_manifest_mismatched_evidence() -> None:
    manifest = _manifest()
    binding = _binding(manifest)

    with pytest.raises(GateInputError, match="exact evidence binding"):
        evaluate_gate(manifest, _scored_cases(manifest), _thresholds())  # type: ignore[arg-type]
    mismatched = binding.model_copy(update={"corpus_id": "other-corpus"})
    with pytest.raises(GateInputError, match="does not match manifest"):
        evaluate_gate(manifest, mismatched, _thresholds())


def test_equivalent_shuffled_inputs_produce_identical_verdict_content() -> None:
    manifest = _manifest()
    scored = _scored_cases(manifest)
    shuffled_manifest = CorpusManifest(
        schema_version="1",
        corpus_id=manifest.corpus_id,
        cases=tuple(reversed(manifest.cases)),
    )

    forward = evaluate_gate(manifest, _binding(manifest, scored), _thresholds())
    reverse = evaluate_gate(
        shuffled_manifest,
        _binding(shuffled_manifest, list(reversed(scored))),
        _thresholds(),
    )

    assert forward.model_dump_json() == reverse.model_dump_json()
