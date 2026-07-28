from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from car_wrap.eval.coverage import evaluate_coverage
from car_wrap.eval.manifest import load_manifest
from car_wrap.eval.models import CorpusCase, CorpusManifest


def _complete_manifest() -> CorpusManifest:
    return load_manifest(Path("eval/corpus.example.yaml"))


def _manifest_with_cases(cases: list[CorpusCase]) -> CorpusManifest:
    return CorpusManifest(
        schema_version="1",
        corpus_id="coverage-test",
        cases=tuple(cases),
    )


def _replace_case(
    case: CorpusCase,
    **updates: object,
) -> CorpusCase:
    return CorpusCase.model_validate({**case.model_dump(), **updates})


def test_complete_locked_corpus_passes_coverage() -> None:
    result = evaluate_coverage(_complete_manifest())

    assert result.complete is True
    assert result.missing_categories == ()


@pytest.mark.parametrize(
    ("keep_vehicle", "missing_category"),
    [
        ("car", "vehicle_type:motorcycle"),
        ("motorcycle", "vehicle_type:car"),
    ],
)
def test_each_vehicle_type_is_required(
    keep_vehicle: str,
    missing_category: str,
) -> None:
    manifest = _complete_manifest()
    filtered = [
        case for case in manifest.cases if case.vehicle_type.value == keep_vehicle
    ]

    result = evaluate_coverage(_manifest_with_cases(filtered))

    assert result.complete is False
    assert missing_category in result.missing_categories


@pytest.mark.parametrize(
    ("remove_case", "missing_category"),
    [
        (
            lambda case: case.viewpoint.value == "front",
            "viewpoint:front",
        ),
        (
            lambda case: case.viewpoint.value == "rear",
            "viewpoint:rear",
        ),
        (
            lambda case: case.viewpoint.value == "side",
            "viewpoint:side",
        ),
        (
            lambda case: case.viewpoint.value == "three_quarter",
            "viewpoint:three_quarter",
        ),
        (
            lambda case: case.source_tone.value == "light",
            "source_tone:light",
        ),
        (
            lambda case: case.source_tone.value == "dark",
            "source_tone:dark",
        ),
    ],
)
def test_each_viewpoint_and_source_tone_is_required(
    remove_case: Callable[[CorpusCase], bool],
    missing_category: str,
) -> None:
    manifest = _complete_manifest()
    filtered = [case for case in manifest.cases if not remove_case(case)]

    result = evaluate_coverage(_manifest_with_cases(filtered))

    assert result.complete is False
    assert result.missing_categories == (missing_category,)


@pytest.mark.parametrize(
    ("field", "missing_category"),
    [
        ("reflections", "reflections:true"),
        ("complex_background", "complex_background:true"),
        ("partial_occlusion", "partial_occlusion:true"),
    ],
)
def test_each_boolean_feature_requires_a_positive_case(
    field: str,
    missing_category: str,
) -> None:
    manifest = _complete_manifest()
    cases = [_replace_case(case, **{field: False}) for case in manifest.cases]

    result = evaluate_coverage(_manifest_with_cases(cases))

    assert result.complete is False
    assert result.missing_categories == (missing_category,)


def test_every_missing_category_is_reported_in_documented_stable_order() -> None:
    one_case = _replace_case(
        _complete_manifest().cases[0],
        reflections=False,
        complex_background=False,
        partial_occlusion=False,
    )

    result = evaluate_coverage(_manifest_with_cases([one_case]))

    assert result.complete is False
    assert result.missing_categories == (
        "vehicle_type:motorcycle",
        "viewpoint:rear",
        "viewpoint:side",
        "viewpoint:three_quarter",
        "source_tone:dark",
        "reflections:true",
        "complex_background:true",
        "partial_occlusion:true",
    )


def test_case_order_does_not_change_byte_ordered_result_content() -> None:
    manifest = _complete_manifest()
    forward = evaluate_coverage(manifest)
    reverse = evaluate_coverage(_manifest_with_cases(list(reversed(manifest.cases))))

    assert forward.model_dump_json() == reverse.model_dump_json()


def test_coverage_uses_validated_fields_not_filenames_or_notes() -> None:
    case = _replace_case(
        _complete_manifest().cases[0],
        source_path="cars/motorcycle-rear-dark-reflections-occlusion.jpg",
        notes="motorcycle rear dark reflections background occlusion",
        reflections=False,
        complex_background=False,
        partial_occlusion=False,
    )

    result = evaluate_coverage(_manifest_with_cases([case]))

    assert "vehicle_type:motorcycle" in result.missing_categories
    assert "viewpoint:rear" in result.missing_categories
    assert "source_tone:dark" in result.missing_categories
    assert "reflections:true" in result.missing_categories
    assert "complex_background:true" in result.missing_categories
    assert "partial_occlusion:true" in result.missing_categories
