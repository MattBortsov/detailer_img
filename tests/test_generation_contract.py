"""Immutable custom-color and two-reference provider contracts."""

from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest

from car_wrap.generation.contracts import (
    BuiltInColorIntent,
    CustomColorIntent,
    SurpriseIntent,
)
from car_wrap.generation.openrouter import build_generation_payload
from car_wrap.palette import PALETTE_CHOICES, SURPRISE_CHOICE


def test_custom_payload_orders_vehicle_then_color_reference() -> None:
    reference = b"canonical-color-png"
    intent = CustomColorIntent(
        color_id=uuid4(),
        version_id=uuid4(),
        version=3,
        sha256=hashlib.sha256(reference).hexdigest(),
        object_key="aa/bb/" + "c" * 32 + ".png",
    )
    payload = build_generation_payload(
        model="x-ai/grok-imagine-image-quality",
        intent=intent,
        vehicle_bytes=b"vehicle",
        vehicle_media_type="image/jpeg",
        color_reference_bytes=reference,
    )

    assert payload["model"] == "x-ai/grok-imagine-image-quality"
    assert len(payload["input_references"]) == 2
    urls = [item["image_url"]["url"] for item in payload["input_references"]]
    assert urls[0].startswith("data:image/jpeg;base64,")
    assert urls[1].startswith("data:image/png;base64,")
    assert "custom" not in payload["prompt"].lower()
    assert intent.object_key not in str(payload)


def test_custom_payload_rejects_wrong_reference_digest() -> None:
    intent = CustomColorIntent(
        color_id=uuid4(),
        version_id=uuid4(),
        version=1,
        sha256="a" * 64,
        object_key="private",
    )
    with pytest.raises(ValueError, match="integrity"):
        build_generation_payload(
            model="x-ai/grok-imagine-image-quality",
            intent=intent,
            vehicle_bytes=b"vehicle",
            vehicle_media_type="image/jpeg",
            color_reference_bytes=b"wrong",
        )


@pytest.mark.parametrize(
    "intent",
    (
        BuiltInColorIntent(PALETTE_CHOICES[0]),
        SurpriseIntent(SURPRISE_CHOICE),
    ),
)
def test_non_custom_intents_have_exactly_one_reference(
    intent: BuiltInColorIntent | SurpriseIntent,
) -> None:
    payload = build_generation_payload(
        model="x-ai/grok-imagine-image-quality",
        intent=intent,
        vehicle_bytes=b"vehicle",
        vehicle_media_type="image/jpeg",
    )
    assert len(payload["input_references"]) == 1
