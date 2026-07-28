"""Evaluation contracts and authorized-fixture validation."""

from car_wrap.eval.models import (
    CaseGateResult,
    CaseScores,
    CorpusCase,
    CorpusManifest,
    CoverageResult,
    FixtureMetadata,
    GateResult,
    GateThresholds,
    RunMetadata,
    ScoredCase,
)
from car_wrap.eval.report import EvaluationReport
from car_wrap.eval.run_manifest import (
    EvidenceBinding,
    GenerationCaseAttempt,
    GenerationRun,
    SafeOutcome,
)

__all__ = [
    "CaseGateResult",
    "CaseScores",
    "CorpusCase",
    "CorpusManifest",
    "CoverageResult",
    "EvaluationReport",
    "EvidenceBinding",
    "FixtureMetadata",
    "GateResult",
    "GateThresholds",
    "GenerationCaseAttempt",
    "GenerationRun",
    "RunMetadata",
    "SafeOutcome",
    "ScoredCase",
]
