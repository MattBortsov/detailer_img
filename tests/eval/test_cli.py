from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from PIL import Image

import car_wrap.eval.__main__ as cli
from car_wrap.eval.models import (
    CaseScores,
    GeneratedImage,
    GeneratedImageMetadata,
    ProviderMetadata,
    ProviderUsage,
    ScoredCase,
)
from car_wrap.eval.run_manifest import (
    GenerationCaseAttempt,
    GenerationRun,
    SafeOutcome,
    load_generation_run,
    write_generation_run,
)


def _workspace(tmp_path: Path) -> tuple[Path, Path, object]:
    raw = yaml.safe_load(Path("eval/corpus.example.yaml").read_text(encoding="utf-8"))
    fixture_root = tmp_path / "fixtures"
    for index, case in enumerate(raw["cases"]):
        buffer = io.BytesIO()
        Image.new("RGB", (2, 2), color=(index, 58, 102)).save(
            buffer,
            format="PNG",
        )
        data = buffer.getvalue()
        fixture = fixture_root / case["source_path"]
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_bytes(data)
        case["source_sha256"] = hashlib.sha256(data).hexdigest()
    manifest_path = tmp_path / "corpus.yaml"
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return manifest_path, fixture_root, cli.load_manifest(manifest_path)


def _run_and_scores(
    tmp_path: Path,
    manifest: object,
    *,
    score: int = 4,
) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    started = datetime(2026, 7, 27, tzinfo=UTC)
    output_sha256 = hashlib.sha256(b"reviewed-output").hexdigest()
    attempts = tuple(
        GenerationCaseAttempt(
            case_id=case.case_id,
            source_sha256=case.source_sha256,
            attempt=1,
            model="openai/gpt-image-2",
            prompt_revision="recolor-v1",
            started_at=started,
            finished_at=started,
            latency_ms=0,
            output_bytes=128,
            output_sha256=output_sha256,
            usage=ProviderUsage(),
            outcome=SafeOutcome(status="succeeded"),
        )
        for case in manifest.cases  # type: ignore[attr-defined]
    )
    run_path = tmp_path / "run.json"
    write_generation_run(
        run_path,
        GenerationRun(
            schema_version="1",
            run_id="cli-test",
            model="openai/gpt-image-2",
            prompt_revision="recolor-v1",
            attempts=attempts,
        ),
    )
    scores = [
        ScoredCase(
            case_id=case.case_id,
            source_sha256=case.source_sha256,
            output_sha256=output_sha256,
            scores=CaseScores.model_validate(
                dict.fromkeys(CaseScores.model_fields, score)
            ),
        ).model_dump(mode="json")
        for case in manifest.cases  # type: ignore[attr-defined]
    ]
    scores_path = tmp_path / "scores.yaml"
    scores_path.write_text(yaml.safe_dump(scores), encoding="utf-8")
    return run_path, scores_path


def test_validate_and_generate_dry_run_are_credential_and_network_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, fixture_root, _ = _workspace(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    class ForbiddenClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("offline command constructed an HTTP client")

    monkeypatch.setattr(cli.httpx, "AsyncClient", ForbiddenClient)
    assert (
        cli.main(
            [
                "validate",
                "--manifest",
                str(manifest_path),
                "--fixture-root",
                str(fixture_root),
            ]
        )
        == cli.EXIT_PASS
    )
    assert (
        cli.main(
            [
                "generate",
                "--manifest",
                str(manifest_path),
                "--fixture-root",
                str(fixture_root),
                "--dry-run",
            ]
        )
        == cli.EXIT_PASS
    )


def test_validate_and_dry_run_reject_truncated_supported_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, fixture_root, _ = _workspace(tmp_path)
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    case = raw["cases"][0]
    corrupt = b"\x89PNG\r\n\x1a\ntruncated"
    (fixture_root / case["source_path"]).write_bytes(corrupt)
    case["source_sha256"] = hashlib.sha256(corrupt).hexdigest()
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    class ForbiddenClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("invalid preflight constructed an HTTP client")

    monkeypatch.setattr(cli.httpx, "AsyncClient", ForbiddenClient)
    assert (
        cli.main(
            [
                "validate",
                "--manifest",
                str(manifest_path),
                "--fixture-root",
                str(fixture_root),
            ]
        )
        == cli.EXIT_INVALID
    )
    assert (
        cli.main(
            [
                "generate",
                "--manifest",
                str(manifest_path),
                "--fixture-root",
                str(fixture_root),
                "--dry-run",
            ]
        )
        == cli.EXIT_INVALID
    )


def test_gate_maps_pass_valid_failure_and_invalid_binding_to_exact_exit_codes(
    tmp_path: Path,
) -> None:
    manifest_path, _, manifest = _workspace(tmp_path)
    run_path, scores_path = _run_and_scores(tmp_path, manifest)
    report = tmp_path / "report.json"
    base = [
        "gate",
        "--manifest",
        str(manifest_path),
        "--run",
        str(run_path),
        "--scores",
        str(scores_path),
        "--thresholds",
        "eval/thresholds.yaml",
        "--report",
        str(report),
    ]

    assert cli.main(base) == cli.EXIT_PASS
    assert '"verdict":"pass"' in report.read_text(encoding="utf-8")

    _, failing_scores = _run_and_scores(tmp_path / "failing", manifest, score=1)
    failing_report = tmp_path / "failing-report.json"
    failing = [*base]
    failing[failing.index(str(scores_path))] = str(failing_scores)
    failing[failing.index(str(report))] = str(failing_report)
    assert cli.main(failing) == cli.EXIT_GATE_FAILED
    assert '"verdict":"fail"' in failing_report.read_text(encoding="utf-8")

    invalid = yaml.safe_load(scores_path.read_text(encoding="utf-8"))
    invalid[0]["source_sha256"] = "f" * 64
    invalid_path = tmp_path / "invalid-scores.yaml"
    invalid_path.write_text(yaml.safe_dump(invalid), encoding="utf-8")
    invalid_command = [*base]
    invalid_command[invalid_command.index(str(scores_path))] = str(invalid_path)
    invalid_command[invalid_command.index(str(report))] = str(
        tmp_path / "must-not-exist.json"
    )
    assert cli.main(invalid_command) == cli.EXIT_INVALID
    assert not (tmp_path / "must-not-exist.json").exists()


def test_gate_requires_the_generation_run_argument() -> None:
    assert (
        cli.main(
            [
                "gate",
                "--manifest",
                "corpus.yaml",
                "--scores",
                "scores.yaml",
                "--thresholds",
                "thresholds.yaml",
                "--report",
                "report.json",
            ]
        )
        == cli.EXIT_INVALID
    )


@pytest.mark.asyncio
async def test_live_generate_calls_provider_once_per_case_and_writes_run_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, fixture_root, manifest = _workspace(tmp_path)
    output_dir = tmp_path / "authorized-output"
    run_path = tmp_path / "phase-01.json"
    calls: list[str] = []

    class DummyClient:
        async def __aenter__(self) -> DummyClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def fake_generate(request: object, **kwargs: object) -> GeneratedImage:
        calls.append(request.fixture.case_id)  # type: ignore[attr-defined]
        return GeneratedImage(
            image_bytes=b"validated-image",
            metadata=GeneratedImageMetadata(
                model="openai/gpt-image-2",
                prompt_revision="recolor-v1",
                latency_ms=1,
                output_bytes=15,
                width=1,
                height=1,
                image_format="png",
                provider=ProviderMetadata(provider="openrouter", status_code=200),
                usage=ProviderUsage(),
            ),
        )

    monkeypatch.setattr(cli, "resolve_output_directory", lambda path: output_dir)
    monkeypatch.setattr(cli.httpx, "AsyncClient", DummyClient)
    monkeypatch.setattr(cli, "generate_image", fake_generate)

    result = await cli._generate_live(
        manifest,
        fixture_root,
        output_dir,
        run_path,
        cli._validate_corpus(manifest_path, fixture_root, cli.EvalSettings())[1],
        cli.EvalSettings(),
    )

    assert result == cli.EXIT_PASS
    assert calls == sorted(case.case_id for case in manifest.cases)
    run = load_generation_run(run_path)
    assert len(run.attempts) == len(manifest.cases)
    assert all(item.outcome.status == "succeeded" for item in run.attempts)
    assert all(
        item.output_sha256 == hashlib.sha256(b"validated-image").hexdigest()
        for item in run.attempts
    )
    assert len(list(output_dir.glob("*.png"))) == len(manifest.cases)

    calls.clear()
    assert (
        await cli._generate_live(
            manifest,
            fixture_root,
            output_dir,
            run_path,
            cli._validate_corpus(manifest_path, fixture_root, cli.EvalSettings())[1],
            cli.EvalSettings(),
        )
        == cli.EXIT_PASS
    )
    assert calls == []

    first_output = sorted(output_dir.glob("*.png"))[0]
    first_output.write_bytes(b"x" * len(b"validated-image"))
    with pytest.raises(cli.CliInputError):
        await cli._generate_live(
            manifest,
            fixture_root,
            output_dir,
            run_path,
            cli._validate_corpus(manifest_path, fixture_root, cli.EvalSettings())[1],
            cli.EvalSettings(),
        )
