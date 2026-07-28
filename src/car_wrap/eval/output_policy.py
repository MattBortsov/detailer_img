"""Authorization policy for operator-owned generated image destinations."""

from __future__ import annotations

import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PROHIBITED_PARTS = {
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "log",
    "logs",
    "report",
    "reports",
    "src",
    "temp",
    "tests",
    "tmp",
}


class OutputPolicyError(ValueError):
    """Fixed-message rejection for an unsafe media destination."""

    def __init__(self) -> None:
        super().__init__("output directory is not authorized")


def _reject_symlink_components(path: Path) -> None:
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise OutputPolicyError


def resolve_output_directory(destination: Path) -> Path:
    """Authorize a real non-symlink descendant of project ``eval/output``.

    Resolution is intentionally non-mutating: callers may create the authorized
    directory only after this function returns.
    """

    try:
        project_root = _PROJECT_ROOT.resolve(strict=True)
        output_root_input = project_root / "eval" / "output"
        candidate_input = (
            destination if destination.is_absolute() else Path.cwd() / destination
        )
        output_root_absolute = output_root_input.absolute()
        candidate_absolute = candidate_input.absolute()
        _reject_symlink_components(output_root_absolute)
        _reject_symlink_components(candidate_absolute)
        output_root = output_root_absolute.resolve(strict=False)
        candidate = candidate_absolute.resolve(strict=False)
        candidate.relative_to(output_root)
    except (OSError, RuntimeError, ValueError):
        raise OutputPolicyError from None

    if candidate == output_root or (candidate.exists() and not candidate.is_dir()):
        raise OutputPolicyError
    if any(part.lower() in _PROHIBITED_PARTS for part in candidate.parts):
        raise OutputPolicyError
    if candidate.suffix.lower() == ".log":
        raise OutputPolicyError

    system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
    try:
        project_root.relative_to(system_temp)
    except ValueError:
        try:
            candidate.relative_to(system_temp)
        except ValueError:
            pass
        else:
            raise OutputPolicyError

    return candidate
