"""Deterministic metadata-only release reports from bound evidence."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field

from car_wrap.eval.gate import GateInputError, evaluate_gate
from car_wrap.eval.models import (
    CaseScores,
    ContractModel,
    CorpusManifest,
    CoverageResult,
    DimensionMeans,
    GateResult,
    GateRule,
    GateThresholds,
    ProviderUsage,
)
from car_wrap.eval.run_manifest import EvidenceBinding, SafeOutcome


class ReportError(ValueError):
    """Fixed-message report validation or write failure."""

    def __init__(self) -> None:
        super().__init__("report evidence validation failed")


class ReportAttempt(ContractModel):
    """Allowlisted attempt measurements safe for persistent review."""

    attempt: int = Field(strict=True, ge=1)
    started_at: datetime
    finished_at: datetime
    latency_ms: int = Field(strict=True, ge=0)
    output_bytes: int | None = Field(default=None, strict=True, ge=0)
    output_sha256: str | None = None
    usage: ProviderUsage | None = None
    cost: Decimal | None = Field(default=None, ge=Decimal("0"))
    peak_rss_bytes: int | None = Field(default=None, strict=True, ge=0)
    outcome: SafeOutcome


class ReportCase(ContractModel):
    """Scores, verdict, and attempt history for one locked source."""

    case_id: str
    source_sha256: str
    output_sha256: str
    scores: CaseScores
    mean_score: Decimal
    passed: bool = Field(strict=True)
    failed_rules: tuple[GateRule, ...]
    selected_attempt: int = Field(strict=True, ge=1)
    attempts: tuple[ReportAttempt, ...]


class EvaluationReport(ContractModel):
    """Complete deterministic QUAL-02/QUAL-03 release evidence."""

    schema_version: Literal["1"]
    corpus_id: str
    run_id: str
    model: str
    prompt_revision: str
    started_at: datetime
    finished_at: datetime
    thresholds: GateThresholds
    cases: tuple[ReportCase, ...]
    dimension_means: DimensionMeans
    case_pass_ratio: Decimal
    case_count: int = Field(strict=True, ge=1)
    passing_case_count: int = Field(strict=True, ge=0)
    coverage: CoverageResult
    failed_rules: tuple[GateRule, ...]
    verdict: Literal["pass", "fail"]


def build_report(
    manifest: CorpusManifest,
    binding: EvidenceBinding,
    thresholds: GateThresholds,
    gate: GateResult,
) -> EvaluationReport:
    """Build a report only from a completely and exactly recomputed gate."""

    if (
        not isinstance(manifest, CorpusManifest)
        or not isinstance(binding, EvidenceBinding)
        or not isinstance(thresholds, GateThresholds)
        or not isinstance(gate, GateResult)
    ):
        raise ReportError
    try:
        recomputed = evaluate_gate(manifest, binding, thresholds)
    except GateInputError:
        raise ReportError from None
    if gate != recomputed:
        raise ReportError
    gate_by_id = {item.case_id: item for item in recomputed.cases}

    report_cases: list[ReportCase] = []
    all_started: list[datetime] = []
    all_finished: list[datetime] = []
    for item in binding.cases:
        evaluated = gate_by_id[item.case_id].result
        attempts = tuple(
            ReportAttempt(
                attempt=attempt.attempt,
                started_at=attempt.started_at,
                finished_at=attempt.finished_at,
                latency_ms=attempt.latency_ms,
                output_bytes=attempt.output_bytes,
                output_sha256=attempt.output_sha256,
                usage=attempt.usage,
                cost=attempt.cost,
                peak_rss_bytes=attempt.peak_rss_bytes,
                outcome=attempt.outcome,
            )
            for attempt in sorted(
                item.attempts,
                key=lambda value: value.attempt,
            )
        )
        all_started.extend(attempt.started_at for attempt in attempts)
        all_finished.extend(attempt.finished_at for attempt in attempts)
        report_cases.append(
            ReportCase(
                case_id=item.case_id,
                source_sha256=item.source_sha256,
                output_sha256=item.output_sha256,
                scores=item.scores,
                mean_score=evaluated.mean_score,
                passed=evaluated.passed,
                failed_rules=tuple(
                    sorted(evaluated.failed_rules, key=lambda rule: rule.value)
                ),
                selected_attempt=item.selected_attempt.attempt,
                attempts=attempts,
            )
        )

    return EvaluationReport(
        schema_version="1",
        corpus_id=binding.corpus_id,
        run_id=binding.run_id,
        model=binding.model,
        prompt_revision=binding.prompt_revision,
        started_at=min(all_started),
        finished_at=max(all_finished),
        thresholds=gate.thresholds,
        cases=tuple(sorted(report_cases, key=lambda item: item.case_id)),
        dimension_means=gate.dimension_means,
        case_pass_ratio=gate.case_pass_ratio,
        case_count=len(report_cases),
        passing_case_count=sum(item.passed for item in report_cases),
        coverage=gate.coverage,
        failed_rules=tuple(sorted(gate.failed_rules, key=lambda rule: rule.value)),
        verdict="pass" if gate.passed else "fail",
    )


def _serialized_bytes(report: EvaluationReport) -> bytes:
    return (
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode()


def write_report(path: Path, report: EvaluationReport) -> None:
    """Atomically write canonical UTF-8 JSON."""

    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(_serialized_bytes(report))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        raise ReportError from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
