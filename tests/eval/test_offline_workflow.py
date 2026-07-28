from __future__ import annotations

from pathlib import Path

import pytest

import car_wrap.eval.__main__ as cli
import car_wrap.eval.output_policy as output_policy
from car_wrap.eval.output_policy import OutputPolicyError, resolve_output_directory


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "project"
    (project / "eval" / "output").mkdir(parents=True)
    monkeypatch.setattr(output_policy, "_PROJECT_ROOT", project)
    return project


def test_output_policy_accepts_only_strict_real_output_descendants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    destination = project / "eval" / "output" / "phase-01"

    assert resolve_output_directory(destination) == destination.resolve(strict=False)
    assert not destination.exists()


@pytest.mark.parametrize(
    "relative",
    [
        ".",
        "src",
        "tests",
        "tmp",
        ".cache",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "eval/output",
        "eval/output/tmp",
        "eval/output/.cache",
        "eval/output/reports",
        "eval/output/logs",
    ],
)
def test_output_policy_rejects_every_prohibited_destination_class(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    project = _project(tmp_path, monkeypatch)

    with pytest.raises(OutputPolicyError, match="not authorized"):
        resolve_output_directory(project / relative)


def test_output_policy_rejects_symlinked_root_and_descendant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    real = project / "operator-media"
    real.mkdir()
    (project / "eval" / "output").rmdir()
    (project / "eval" / "output").symlink_to(real, target_is_directory=True)
    with pytest.raises(OutputPolicyError):
        resolve_output_directory(project / "eval" / "output" / "phase-01")

    (project / "eval" / "output").unlink()
    (project / "eval" / "output").mkdir()
    (project / "eval" / "output" / "linked").symlink_to(real, target_is_directory=True)
    with pytest.raises(OutputPolicyError):
        resolve_output_directory(project / "eval" / "output" / "linked")


def test_unsafe_output_fails_before_generation_or_filesystem_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    invoked = False

    def forbidden_run(*args: object, **kwargs: object) -> int:
        nonlocal invoked
        invoked = True
        raise AssertionError("generation boundary was reached")

    monkeypatch.setattr(cli, "_generate_live", forbidden_run)
    with pytest.raises(OutputPolicyError):
        resolve_output_directory(project / "eval" / "output" / "reports")
    assert invoked is False
    assert not (project / "eval" / "output" / "reports").exists()


def test_tracked_contracts_are_not_ignored() -> None:
    for path in (
        Path("eval/corpus.example.yaml"),
        Path("eval/thresholds.yaml"),
        Path("eval/README.md"),
    ):
        assert path.exists()
