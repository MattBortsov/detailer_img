from __future__ import annotations

import tomllib
from collections.abc import Iterator, MutableMapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from car_wrap.config import EvalSettings
from car_wrap.eval.models import (
    CaseScores,
    ContractModel,
    CorpusCase,
    CorpusManifest,
    FixtureMetadata,
    GateThresholds,
    ProviderMetadata,
    ProviderUsage,
    RunMetadata,
    SafeError,
)

SCORE_DIMENSIONS = {
    "vehicle_identity",
    "geometry_viewpoint",
    "target_coverage",
    "non_target_preservation",
    "lighting_material",
    "color_intent",
    "artifact_control",
    "telegram_usability",
}


def _case_data() -> dict[str, object]:
    return {
        "case_id": "car-front-light",
        "source_path": "cars/front-light.jpg",
        "source_sha256": "a" * 64,
        "vehicle_type": "car",
        "viewpoint": "front",
        "source_tone": "light",
        "reflections": True,
        "complex_background": False,
        "partial_occlusion": False,
        "color_id": "deep-blue",
    }


def _score_data(value: int = 4) -> dict[str, int]:
    return dict.fromkeys(SCORE_DIMENSIONS, value)


def _threshold_data() -> dict[str, object]:
    minimum_scores = _score_data(3)
    minimum_scores["vehicle_identity"] = 4
    minimum_scores["geometry_viewpoint"] = 4
    minimum_scores["non_target_preservation"] = 4
    return {
        "schema_version": "1",
        "minimum_scores": minimum_scores,
        "minimum_mean": 4.0,
        "minimum_case_pass_ratio": 0.8,
        "critical_failure_floor": 3,
    }


def _model_examples() -> list[tuple[type[ContractModel], dict[str, object]]]:
    return [
        (CorpusCase, _case_data()),
        (
            CorpusManifest,
            {
                "schema_version": "1",
                "corpus_id": "feasibility-v1",
                "cases": [_case_data()],
            },
        ),
        (CaseScores, _score_data()),
        (GateThresholds, _threshold_data()),
        (
            ProviderUsage,
            {
                "input_tokens": 12,
                "output_tokens": 34,
                "total_tokens": 46,
                "cost_usd": Decimal("0.042"),
            },
        ),
        (
            ProviderMetadata,
            {
                "provider": "openrouter",
                "status_code": 200,
                "request_id": "req_safe-123",
            },
        ),
        (
            RunMetadata,
            {
                "schema_version": "1",
                "run_id": "run-20260727",
                "model": "openai/gpt-image-2",
                "prompt_revision": "recolor-v1",
                "started_at": datetime(2026, 7, 27, tzinfo=UTC),
                "latency_ms": 1_250,
                "output_bytes": 512_000,
                "peak_rss_bytes": 96_000_000,
                "provider": {
                    "provider": "openrouter",
                    "status_code": 200,
                    "request_id": "req_safe-123",
                },
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 34,
                    "total_tokens": 46,
                    "cost_usd": Decimal("0.042"),
                },
            },
        ),
        (
            FixtureMetadata,
            {
                "case_id": "car-front-light",
                "byte_count": 512_000,
                "sha256": "a" * 64,
                "source_media_type": "image/png",
            },
        ),
        (SafeError, {"code": "invalid_manifest"}),
    ]


@pytest.mark.parametrize(("model_type", "data"), _model_examples())
def test_boundary_models_are_frozen_and_forbid_unknown_fields(
    model_type: type[ContractModel],
    data: dict[str, object],
) -> None:
    assert model_type.model_config.get("frozen") is True
    assert model_type.model_config.get("extra") == "forbid"

    instance = model_type.model_validate(data)
    with pytest.raises(ValidationError):
        model_type.model_validate({**data, "unexpected": "not allowed"})
    with pytest.raises(ValidationError):
        instance.__setattr__(next(iter(model_type.model_fields)), "changed")


def test_case_scores_expose_exactly_eight_locked_integer_dimensions() -> None:
    assert set(CaseScores.model_fields) == SCORE_DIMENSIONS

    for field in CaseScores.model_fields.values():
        assert field.annotation is int
        constraints = {type(item).__name__: item for item in field.metadata}
        assert constraints["Ge"].ge == 1
        assert constraints["Le"].le == 5
        assert constraints["Strict"].strict is True

    CaseScores.model_validate(_score_data(1))
    CaseScores.model_validate(_score_data(5))

    for invalid in (0, 6, "5", 4.5):
        data = _score_data()
        data["vehicle_identity"] = invalid  # type: ignore[assignment]
        with pytest.raises(ValidationError):
            CaseScores.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("vehicle_type", "truck"),
        ("viewpoint", "overhead"),
        ("source_tone", "medium"),
        ("source_sha256", "not-a-sha256"),
        ("source_path", "/private/authorized/car.jpg"),
        ("source_path", "../outside.jpg"),
        ("source_path", "https://example.test/car.jpg"),
    ],
)
def test_corpus_case_rejects_invalid_enum_and_fixture_values(
    field: str,
    value: object,
) -> None:
    data = _case_data()
    data[field] = value

    with pytest.raises(ValidationError):
        CorpusCase.model_validate(data)


class _ApiKeyTrap(MutableMapping[str, str]):
    def __init__(self) -> None:
        self._values = {
            "OPENROUTER_IMAGE_MODEL": "operator/alternate-image-model",
            "OPENROUTER_API_KEY": "must-never-be-read-offline",
        }

    def __getitem__(self, key: str) -> str:
        if key == "OPENROUTER_API_KEY":
            raise AssertionError("offline settings read OPENROUTER_API_KEY")
        return self._values[key]

    def __setitem__(self, key: str, value: str) -> None:
        self._values[key] = value

    def __delitem__(self, key: str) -> None:
        del self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "OPENROUTER_API_KEY":
            raise AssertionError("offline settings read OPENROUTER_API_KEY")
        return self._values.get(key, default)


def test_offline_settings_do_not_read_openrouter_api_key() -> None:
    default_settings = EvalSettings()
    assert default_settings.openrouter_image_model == "x-ai/grok-imagine-image-quality"

    settings = EvalSettings.from_environment(_ApiKeyTrap())

    assert settings.openrouter_image_model == "operator/alternate-image-model"
    assert "api_key" not in settings.model_dump()
    assert all("api_key" not in name for name in type(settings).model_fields)


REPORT_MODELS = (
    CaseScores,
    GateThresholds,
    ProviderUsage,
    ProviderMetadata,
    RunMetadata,
    FixtureMetadata,
    SafeError,
)


def test_report_models_have_no_unsafe_serializable_field_names() -> None:
    forbidden_field_fragments = {
        "path",
        "bytes_data",
        "base64",
        "data_url",
        "signed_url",
        "url",
        "payload",
        "exception",
        "traceback",
        "headers",
        "token",
        "api_key",
    }

    for model_type in REPORT_MODELS:
        field_names = set(model_type.model_fields)
        assert field_names.isdisjoint(forbidden_field_fragments)


@pytest.mark.parametrize(
    "unsafe_field",
    [
        "path",
        "image_bytes",
        "base64",
        "data_url",
        "signed_url",
        "payload",
        "exception",
        "provider_response",
    ],
)
def test_report_models_reject_unsafe_extra_fields(unsafe_field: str) -> None:
    for model_type, data in _model_examples():
        if model_type not in REPORT_MODELS:
            continue
        with pytest.raises(ValidationError):
            model_type.model_validate(
                {
                    **data,
                    unsafe_field: (
                        b"privacy-canary"
                        if "bytes" in unsafe_field
                        else "privacy-canary"
                    ),
                }
            )


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "data:image/png;base64,cHJpdmFjeS1jYW5hcnk=",
        "https://media.example.test/output.png?X-Amz-Signature=privacy-canary",
        "/private/authorized/fixture.jpg",
        "Bearer privacy-canary-token",
    ],
)
def test_safe_string_fields_reject_transport_and_path_canaries(
    unsafe_value: str,
) -> None:
    data = _model_examples()[6][1].copy()
    data["model"] = unsafe_value
    with pytest.raises(ValidationError):
        RunMetadata.model_validate(data)

    with pytest.raises(ValidationError):
        SafeError.model_validate({"code": "invalid_manifest", "message": unsafe_value})


def test_project_dependencies_do_not_add_queue_or_provider_clients() -> None:
    pyproject_path = Path(__file__).parents[2] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    optional_groups = pyproject["project"].get("optional-dependencies", {})
    all_dependencies = dependencies + [
        dependency for group in optional_groups.values() for dependency in group
    ]
    normalized = " ".join(all_dependencies).lower()

    required_phase2 = {
        "aiogram==3.30.0",
        "fastapi==0.140.0",
        "SQLAlchemy==2.0.51",
        "alembic==1.18.5",
        "psycopg[binary]==3.3.4",
        "uvicorn[standard]==0.51.0",
    }
    assert required_phase2.issubset(set(dependencies))

    forbidden = ("redis", "celery", "openai", "openrouter")
    assert not any(name in normalized for name in forbidden)
