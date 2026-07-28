"""Pure OpenRouter Images payload builder for typed recoloring intents."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from car_wrap.generation.contracts import (
    BuiltInColorIntent,
    CustomColorIntent,
    SurpriseIntent,
)
from car_wrap.prompting import build_recolor_prompt


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
        if (
            color_reference_bytes is None
            or hashlib.sha256(color_reference_bytes).hexdigest() != intent.sha256
        ):
            raise ValueError("custom color reference integrity mismatch")
        references.append(
            {
                "type": "image_url",
                "image_url": {"url": _data_url(color_reference_bytes, "image/png")},
            }
        )
        prompt = (
            "Use the first image as the vehicle source and the second image "
            "only as the target wrap color and finish reference. Recolor all "
            "visible painted vehicle body surfaces to match that material. "
            "Preserve vehicle geometry, identity, viewpoint, parts, lighting, "
            "reflections, background, wheels, glass, trim, badges and plates."
        )
    return {
        "model": model,
        "prompt": prompt,
        "input_references": references,
    }
