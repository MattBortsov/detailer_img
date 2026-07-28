"""Pure deterministic evaluation of locked corpus coverage."""

from __future__ import annotations

from car_wrap.eval.models import (
    CorpusManifest,
    CoverageCategory,
    CoverageResult,
    SourceTone,
    VehicleType,
    Viewpoint,
)

COVERAGE_CATEGORY_ORDER: tuple[CoverageCategory, ...] = tuple(CoverageCategory)


def evaluate_coverage(manifest: CorpusManifest) -> CoverageResult:
    """Evaluate every D-03 category without file access or short-circuiting."""

    vehicle_types = {case.vehicle_type for case in manifest.cases}
    viewpoints = {case.viewpoint for case in manifest.cases}
    source_tones = {case.source_tone for case in manifest.cases}
    present = {
        CoverageCategory.VEHICLE_CAR: VehicleType.CAR in vehicle_types,
        CoverageCategory.VEHICLE_MOTORCYCLE: (VehicleType.MOTORCYCLE in vehicle_types),
        CoverageCategory.VIEWPOINT_FRONT: Viewpoint.FRONT in viewpoints,
        CoverageCategory.VIEWPOINT_REAR: Viewpoint.REAR in viewpoints,
        CoverageCategory.VIEWPOINT_SIDE: Viewpoint.SIDE in viewpoints,
        CoverageCategory.VIEWPOINT_THREE_QUARTER: (
            Viewpoint.THREE_QUARTER in viewpoints
        ),
        CoverageCategory.SOURCE_TONE_LIGHT: SourceTone.LIGHT in source_tones,
        CoverageCategory.SOURCE_TONE_DARK: SourceTone.DARK in source_tones,
        CoverageCategory.REFLECTIONS: any(case.reflections for case in manifest.cases),
        CoverageCategory.COMPLEX_BACKGROUND: any(
            case.complex_background for case in manifest.cases
        ),
        CoverageCategory.PARTIAL_OCCLUSION: any(
            case.partial_occlusion for case in manifest.cases
        ),
    }
    missing = tuple(
        category for category in COVERAGE_CATEGORY_ORDER if not present[category]
    )
    return CoverageResult(
        complete=not missing,
        missing_categories=missing,
    )
