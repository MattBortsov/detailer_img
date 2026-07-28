"""Offline-first command-line orchestration for the evaluation contract."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import TypeAdapter, ValidationError

from car_wrap.config import EvalSettings
from car_wrap.eval.coverage import evaluate_coverage
from car_wrap.eval.gate import GateInputError, evaluate_gate
from car_wrap.eval.manifest import (
    FixtureValidationError,
    ManifestLoadError,
    load_manifest,
    validate_fixture,
)
from car_wrap.eval.models import (
    CorpusManifest,
    FixtureMetadata,
    GateThresholds,
    ImageGenerationRequest,
    ProviderError,
    ScoredCase,
)
from car_wrap.eval.openrouter import generate_image
from car_wrap.eval.output_policy import OutputPolicyError, resolve_output_directory
from car_wrap.eval.report import ReportError, build_report, write_report
from car_wrap.eval.run_manifest import (
    EvidenceBindingError,
    GenerationCaseAttempt,
    GenerationRun,
    GenerationRunError,
    SafeOutcome,
    load_generation_run,
    validate_evidence_binding,
    write_generation_run,
)
from car_wrap.palette import EVALUATION_COLORS
from car_wrap.prompting import PROMPT_REVISION

EXIT_PASS = 0
EXIT_GATE_FAILED = 1
EXIT_INVALID = 2
EXIT_PROVIDER_FAILED = 3

_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SCORES = TypeAdapter(list[ScoredCase])


class CliInputError(ValueError):
    """Fixed-message invalid command evidence."""

    def __init__(self) -> None:
        super().__init__("evaluation input validation failed")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m car_wrap.eval")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--fixture-root", type=Path, required=True)

    gate = commands.add_parser("gate")
    gate.add_argument("--manifest", type=Path, required=True)
    gate.add_argument("--run", type=Path, required=True)
    gate.add_argument("--scores", type=Path, required=True)
    gate.add_argument("--thresholds", type=Path, required=True)
    gate.add_argument("--report", type=Path, required=True)

    generate = commands.add_parser("generate")
    generate.add_argument("--manifest", type=Path, required=True)
    generate.add_argument("--fixture-root", type=Path, required=True)
    generate.add_argument("--output-dir", type=Path)
    generate.add_argument("--run", type=Path)
    generate.add_argument("--dry-run", action="store_true")
    return parser


def _load_yaml(path: Path) -> Any:
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            raise CliInputError
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except CliInputError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError):
        raise CliInputError from None


def _load_scores(path: Path) -> list[ScoredCase]:
    try:
        return _SCORES.validate_python(_load_yaml(path))
    except ValidationError:
        raise CliInputError from None


def _load_thresholds(path: Path) -> GateThresholds:
    try:
        return GateThresholds.model_validate(_load_yaml(path))
    except ValidationError:
        raise CliInputError from None


def _validate_corpus(
    manifest_path: Path,
    fixture_root: Path,
    settings: EvalSettings,
) -> tuple[CorpusManifest, dict[str, FixtureMetadata]]:
    manifest = load_manifest(manifest_path)
    if not evaluate_coverage(manifest).complete:
        raise CliInputError
    metadata = {
        case.case_id: validate_fixture(
            fixture_root,
            case,
            max_bytes=settings.fixture_max_bytes,
            max_width=settings.provider_max_image_width,
            max_height=settings.provider_max_image_height,
            max_pixels=settings.provider_max_image_pixels,
        )
        for case in sorted(manifest.cases, key=lambda item: item.case_id)
    }
    return manifest, metadata


def _validate_recorded_output(
    output_dir: Path,
    attempt: GenerationCaseAttempt,
) -> None:
    candidates = [
        output_dir / f"{attempt.case_id}.{extension}"
        for extension in ("png", "jpeg", "webp")
        if (output_dir / f"{attempt.case_id}.{extension}").exists()
    ]
    if (
        len(candidates) != 1
        or candidates[0].is_symlink()
        or not candidates[0].is_file()
        or attempt.output_bytes is None
        or attempt.output_sha256 is None
    ):
        raise CliInputError
    output = candidates[0]
    try:
        if output.stat().st_size != attempt.output_bytes:
            raise CliInputError
        digest = hashlib.sha256()
        byte_count = 0
        with output.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                byte_count += len(chunk)
                if byte_count > attempt.output_bytes:
                    raise CliInputError
                digest.update(chunk)
    except CliInputError:
        raise
    except OSError:
        raise CliInputError from None
    if (
        byte_count != attempt.output_bytes
        or digest.hexdigest() != attempt.output_sha256
    ):
        raise CliInputError


def _validate_existing_run(
    run: GenerationRun,
    manifest: CorpusManifest,
    settings: EvalSettings,
    output_dir: Path,
) -> None:
    manifest_by_id = {case.case_id: case for case in manifest.cases}
    if (
        run.model != settings.openrouter_image_model
        or run.prompt_revision != PROMPT_REVISION
        or any(
            attempt.case_id not in manifest_by_id
            or attempt.source_sha256 != manifest_by_id[attempt.case_id].source_sha256
            for attempt in run.attempts
        )
    ):
        raise CliInputError
    successes = [
        attempt for attempt in run.attempts if attempt.outcome.status == "succeeded"
    ]
    successful_case_ids = [attempt.case_id for attempt in successes]
    if len(successful_case_ids) != len(set(successful_case_ids)):
        raise CliInputError
    for attempt in successes:
        _validate_recorded_output(output_dir, attempt)


async def _generate_live(
    manifest: CorpusManifest,
    fixture_root: Path,
    output_dir: Path,
    run_path: Path,
    metadata: dict[str, FixtureMetadata],
    settings: EvalSettings,
) -> int:
    if await asyncio.to_thread(run_path.exists):
        run = await asyncio.to_thread(load_generation_run, run_path)
        await asyncio.to_thread(
            _validate_existing_run,
            run,
            manifest,
            settings,
            output_dir,
        )
        attempts = list(run.attempts)
        run_id = run.run_id
    else:
        run_id = run_path.stem
        if not _RUN_ID.fullmatch(run_id):
            raise CliInputError
        attempts = []

    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(run_path.parent.mkdir, parents=True, exist_ok=True)
    had_provider_failure = False
    successful = {
        item.case_id for item in attempts if item.outcome.status == "succeeded"
    }
    async with httpx.AsyncClient() as client:
        for case in sorted(manifest.cases, key=lambda item: item.case_id):
            if case.case_id in successful:
                continue
            attempt_number = (
                max(
                    (item.attempt for item in attempts if item.case_id == case.case_id),
                    default=0,
                )
                + 1
            )
            source_path = await asyncio.to_thread(fixture_root.resolve, strict=True)
            source = await asyncio.to_thread(
                (source_path / case.source_path).read_bytes
            )
            request = ImageGenerationRequest(
                source_bytes=source,
                source_media_type=metadata[case.case_id].source_media_type,
                fixture=metadata[case.case_id],
                color=EVALUATION_COLORS.get(case.color_id)
                or (_ for _ in ()).throw(CliInputError()),
            )
            started = datetime.now(UTC)
            try:
                generated = await generate_image(
                    request,
                    client=client,
                    settings=settings,
                )
            except ProviderError as exc:
                finished = datetime.now(UTC)
                attempts.append(
                    GenerationCaseAttempt(
                        case_id=case.case_id,
                        source_sha256=case.source_sha256,
                        attempt=attempt_number,
                        model=settings.openrouter_image_model,
                        prompt_revision=PROMPT_REVISION,
                        started_at=started,
                        finished_at=finished,
                        latency_ms=max(
                            0, int((finished - started).total_seconds() * 1000)
                        ),
                        outcome=SafeOutcome(
                            status="failed",
                            error_code=exc.code,
                            status_code=exc.status_code,
                        ),
                    )
                )
                had_provider_failure = True
            else:
                finished = datetime.now(UTC)
                extension = generated.metadata.image_format
                if generated.metadata.output_bytes != len(generated.image_bytes):
                    raise CliInputError
                output_sha256 = hashlib.sha256(generated.image_bytes).hexdigest()
                await asyncio.to_thread(
                    (output_dir / f"{case.case_id}.{extension}").write_bytes,
                    generated.image_bytes,
                )
                attempts.append(
                    GenerationCaseAttempt(
                        case_id=case.case_id,
                        source_sha256=case.source_sha256,
                        attempt=attempt_number,
                        model=settings.openrouter_image_model,
                        prompt_revision=PROMPT_REVISION,
                        started_at=started,
                        finished_at=finished,
                        latency_ms=generated.metadata.latency_ms,
                        output_bytes=generated.metadata.output_bytes,
                        output_sha256=output_sha256,
                        usage=generated.metadata.usage,
                        cost=generated.metadata.usage.cost_usd,
                        outcome=SafeOutcome(status="succeeded"),
                    )
                )
            run = GenerationRun(
                schema_version="1",
                run_id=run_id,
                model=settings.openrouter_image_model,
                prompt_revision=PROMPT_REVISION,
                attempts=tuple(attempts),
            )
            await asyncio.to_thread(write_generation_run, run_path, run)
    return EXIT_PROVIDER_FAILED if had_provider_failure else EXIT_PASS


def _run_validate(args: argparse.Namespace) -> int:
    settings = EvalSettings.from_environment()
    _validate_corpus(args.manifest, args.fixture_root, settings)
    return EXIT_PASS


def _run_gate(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    run = load_generation_run(args.run)
    scores = _load_scores(args.scores)
    thresholds = _load_thresholds(args.thresholds)
    binding = validate_evidence_binding(manifest, run, scores)
    result = evaluate_gate(manifest, binding, thresholds)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    write_report(
        args.report,
        build_report(manifest, binding, thresholds, result),
    )
    return EXIT_PASS if result.passed else EXIT_GATE_FAILED


def _run_generate(args: argparse.Namespace) -> int:
    settings = EvalSettings.from_environment()
    manifest, metadata = _validate_corpus(
        args.manifest,
        args.fixture_root,
        settings,
    )
    if args.dry_run:
        return EXIT_PASS
    if args.output_dir is None or args.run is None:
        raise CliInputError
    output_dir = resolve_output_directory(args.output_dir)
    return asyncio.run(
        _generate_live(
            manifest,
            args.fixture_root,
            output_dir,
            args.run,
            metadata,
            settings,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run a CLI command and map every outcome to its documented exit code."""

    try:
        args = _parser().parse_args(argv)
        if args.command == "validate":
            return _run_validate(args)
        if args.command == "gate":
            return _run_gate(args)
        if args.command == "generate":
            return _run_generate(args)
        raise CliInputError
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_INVALID
    except ProviderError:
        return EXIT_PROVIDER_FAILED
    except (
        CliInputError,
        EvidenceBindingError,
        FixtureValidationError,
        GateInputError,
        GenerationRunError,
        ManifestLoadError,
        OSError,
        OutputPolicyError,
        ReportError,
        ValidationError,
        ValueError,
    ):
        return EXIT_INVALID


if __name__ == "__main__":
    raise SystemExit(main())
