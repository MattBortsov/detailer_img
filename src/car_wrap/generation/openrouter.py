"""Pure OpenRouter Images payload builder for typed recoloring intents."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from car_wrap.custom_colors.analysis import ReferenceProfile, SurfaceFinish
from car_wrap.generation.contracts import (
    BuiltInColorIntent,
    CustomColorIntent,
    SurpriseIntent,
)
from car_wrap.prompting import build_recolor_prompt

_FINISH_RESPONSE = {
    SurfaceFinish.MATTE.value: (
        "Use a matte material response with broad diffuse highlights and "
        "minimal mirror-like reflections."
    ),
    SurfaceFinish.SATIN.value: (
        "Use a satin material response with softly diffused highlights and "
        "restrained environment reflections."
    ),
    SurfaceFinish.GLOSS.value: (
        "Use a high-gloss material response with crisp specular highlights "
        "and clear environment reflections."
    ),
}


def _data_url(data: bytes, media_type: str) -> str:
    if not data or media_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError("invalid provider image reference")
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


def build_generation_payload(
    *,
    model: str,
    intent: BuiltInColorIntent | CustomColorIntent | SurpriseIntent,
    vehicle_bytes: bytes,
    vehicle_media_type: str,
    color_reference_bytes: bytes | None = None,
) -> dict[str, Any]:
    references = [
        {
            "type": "image_url",
            "image_url": {"url": _data_url(vehicle_bytes, vehicle_media_type)},
        }
    ]
    if isinstance(intent, BuiltInColorIntent):
        if color_reference_bytes is not None:
            raise ValueError("built-in colors do not accept a second reference")
        prompt = build_recolor_prompt(
            intent.choice.to_evaluation_color(),
        )
    elif isinstance(intent, SurpriseIntent):
        if color_reference_bytes is not None:
            raise ValueError("surprise does not accept a second reference")
        prompt = (
            "Recolor all visible painted vehicle body surfaces with one "
            "stylish finish suitable for the vehicle and scene. Preserve "
            "vehicle geometry, identity, viewpoint, parts, lighting, "
            "reflections, background, wheels, glass, trim, badges and plates."
        )
    else:
        expected_reference_sha256 = intent.provider_reference_sha256 or intent.sha256
        if (
            color_reference_bytes is None
            or hashlib.sha256(color_reference_bytes).hexdigest()
            != expected_reference_sha256
        ):
            raise ValueError("custom color reference integrity mismatch")
        references.append(
            {
                "type": "image_url",
                "image_url": {"url": _data_url(color_reference_bytes, "image/png")},
            }
        )
        if intent.color_profile is None:
            material_intent = (
                "Treat the second image as the sole target wrap color and finish "
                "reference."
            )
        else:
            profile = ReferenceProfile.from_dict(intent.color_profile)
            finish_response = _FINISH_RESPONSE[intent.finish]
            if intent.color_structure == "solid":
                material_intent = (
                    "Treat the second image as the sole target material reference. "
                    f"Its authoritative sRGB base color is {profile.base_rgb_hex} "
                    f"and its finish is {intent.finish}. {finish_response}"
                )
            else:
                palette_text = ", ".join(
                    f"{entry.rgb_hex} at {round(entry.weight * 100)}%"
                    for entry in profile.palette
                )
                material_intent = (
                    "Treat the second image as the sole target material reference. "
                    "It represents an angle-dependent multicolor wrap with the "
                    f"weighted palette {palette_text} and {intent.finish} finish. "
                    f"{finish_response} "
                    "Express the palette through material response to light and "
                    "viewing angle; do not reproduce its layout as stripes, panels, "
                    "decals, or a literal spatial gradient."
                )
        prompt = (
            "Use the first image as the vehicle source. "
            f"{material_intent} Recolor all visible painted vehicle body surfaces "
            "to match that material. Preserve vehicle geometry, identity, viewpoint, "
            "parts, background, wheels, glass, trim, badges and plates. Preserve the "
            "scene's illumination direction, exposure, shadow placement, and reflected "
            "surroundings. Adapt the painted surfaces' highlight sharpness and "
            "reflection strength to the target finish."
        )
    return {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "resolution": "1K",
        "input_references": references,
    }
