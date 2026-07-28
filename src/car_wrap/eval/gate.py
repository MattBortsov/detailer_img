"""Pure preservation-first case and release gate evaluation."""

from __future__ import annotations

from decimal import Decimal

from car_wrap.eval.coverage import evaluate_coverage
from car_wrap.eval.models import (
    CaseGateResult,
    CaseScores,
    CorpusManifest,
    DimensionMeans,
    EvaluatedCase,
    GateResult,
    GateRule,
    GateThresholds,
    ScoredCase,
)
from car_wrap.eval.run_manifest import EvidenceBinding

_DIMENSION_RULES: tuple[tuple[str, GateRule], ...] = (
    ("vehicle_identity", GateRule.MINIMUM_VEHICLE_IDENTITY),
    ("geometry_viewpoint", GateRule.MINIMUM_GEOMETRY_VIEWPOINT),
    ("target_coverage", GateRule.MINIMUM_TARGET_COVERAGE),
    (
        "non_target_preservation",
        GateRule.MINIMUM_NON_TARGET_PRESERVATION,
    ),
    ("lighting_material", GateRule.MINIMUM_LIGHTING_MATERIAL),
    ("color_intent", GateRule.MINIMUM_COLOR_INTENT),
    ("artifact_control", GateRule.MINIMUM_ARTIFACT_CONTROL),
    ("telegram_usability", GateRule.MINIMUM_TELEGRAM_USABILITY),
)


class GateInputError(ValueError):
    """Invalid evidence binding, distinct from a valid failing gate."""


def evaluate_case(
    scores: CaseScores,
    thresholds: GateThresholds,
) -> CaseGateResult:
    """Apply every dimension and mean floor in documented stable order."""

    failed = [
        rule
        for dimension, rule in _DIMENSION_RULES
        if getattr(scores, dimension) < getattr(thresholds.minimum_scores, dimension)
    ]
    score_sum = sum(getattr(scores, name) for name, _ in _DIMENSION_RULES)
    mean_score = Decimal(score_sum) / Decimal(len(_DIMENSION_RULES))
    if mean_score < Decimal(str(thresholds.minimum_mean)):
        failed.append(GateRule.MINIMUM_MEAN)
    return CaseGateResult(
        scores=scores,
        mean_score=mean_score,
        passed=not failed,
        failed_rules=tuple(failed),
    )


def evaluate_gate(
    manifest: CorpusManifest,
    binding: EvidenceBinding,
    thresholds: GateThresholds,
) -> GateResult:
    """Evaluate only exact-bound generation and human-score evidence."""

    if not isinstance(binding, EvidenceBinding):
        raise GateInputError("gate requires exact evidence binding")
    manifest_by_id = {case.case_id: case for case in manifest.cases}
    binding_by_id = {case.case_id: case for case in binding.cases}
    if (
        binding.corpus_id != manifest.corpus_id
        or len(binding_by_id) != len(binding.cases)
        or set(binding_by_id) != set(manifest_by_id)
        or any(
            binding_by_id[case_id].source_sha256
            != manifest_by_id[case_id].source_sha256
            for case_id in manifest_by_id
        )
    ):
        raise GateInputError("bound evidence does not match manifest")

    ordered_scores = tuple(
        ScoredCase(
            case_id=case.case_id,
            source_sha256=case.source_sha256,
            output_sha256=case.output_sha256,
            scores=case.scores,
        )
        for case in binding.cases
    )
    cases = tuple(
        EvaluatedCase(
            case_id=scored.case_id,
            result=evaluate_case(scored.scores, thresholds),
        )
        for scored in ordered_scores
    )
    case_count = len(cases)
    pass_count = sum(case.result.passed for case in cases)
    case_pass_ratio = Decimal(pass_count) / Decimal(case_count)
    dimension_means = DimensionMeans.model_validate(
        {
            dimension: Decimal(
                sum(getattr(case.scores, dimension) for case in ordered_scores)
            )
            / Decimal(case_count)
            for dimension, _ in _DIMENSION_RULES
        }
    )

    coverage = evaluate_coverage(manifest)
    failed: list[GateRule] = []
    if not coverage.complete:
        failed.append(GateRule.CORPUS_COVERAGE)
    if any(
        case.scores.vehicle_identity < thresholds.critical_failure_floor
        for case in ordered_scores
    ):
        failed.append(GateRule.ZERO_CRITICAL_IDENTITY)
    if any(
        case.scores.geometry_viewpoint < thresholds.critical_failure_floor
        for case in ordered_scores
    ):
        failed.append(GateRule.ZERO_CRITICAL_GEOMETRY)
    if case_pass_ratio < Decimal(str(thresholds.minimum_case_pass_ratio)):
        failed.append(GateRule.MINIMUM_CASE_PASS_RATIO)

    return GateResult(
        passed=not failed,
        coverage=coverage,
        thresholds=thresholds,
        cases=cases,
        dimension_means=dimension_means,
        case_pass_ratio=case_pass_ratio,
        failed_rules=tuple(failed),
    )
