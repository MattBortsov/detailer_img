"""Audited server-owned prompt contract for vehicle recoloring."""

from __future__ import annotations

from car_wrap.eval.models import EvaluationColor

PROMPT_REVISION = "recolor-v1"


def build_recolor_prompt(color: EvaluationColor) -> str:
    """Build the deterministic preservation-first prompt for one palette color."""

    if not isinstance(color, EvaluationColor):
        raise TypeError("prompt requires a validated evaluation color")

    return (
        "Create one realistic visualization by recoloring all visible painted "
        "body surfaces of the vehicle to the server-owned color intent "
        f"{color.display_name} ({color.rgb_hex}, id {color.color_id}). "
        "Preserve the vehicle identity, silhouette, geometry, panel layout, "
        "viewpoint, and crop. Preserve all non-target elements, including lights, "
        "glass, wheels, tires, trim, plates, motorcycle mechanical parts, people, "
        "and the background. Preserve the existing lighting, shadows, reflections, "
        "materials, perspective, and scene composition. Do not add or remove "
        "objects; do not redesign or restyle the vehicle; do not recrop or "
        "reposition the vehicle or any scene element."
    )
