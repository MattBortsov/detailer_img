"""Strict fail-closed custom color moderation contracts."""

from __future__ import annotations

import json

import httpx
import pytest

from car_wrap.custom_colors.moderation import (
    ModerationDisposition,
    build_moderation_payload,
    moderate_reference,
    normalize_display_name,
)


def response_payload(**overrides: object) -> dict[str, object]:
    decision: dict[str, object] = {
        "safe": True,
        "sexual": False,
        "violence": False,
        "other_unsafe": False,
        "wrap_reference": True,
        "safety_confidence": 98,
        "domain_confidence": 96,
        "material_regions": [{"x": 100, "y": 120, "width": 700, "height": 650}],
        "excluded_regions": [{"x": 300, "y": 300, "width": 120, "height": 80}],
        "localization_confidence": 93,
        "label_name": None,
        "product_code": None,
        "reason_code": "approved",
    }
    decision.update(overrides)
    return {"choices": [{"message": {"content": json.dumps(decision)}}]}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    (
        ({}, ModerationDisposition.APPROVED),
        ({"sexual": True, "safe": False}, ModerationDisposition.REJECTED),
        ({"violence": True, "safe": False}, ModerationDisposition.REJECTED),
        ({"wrap_reference": False}, ModerationDisposition.NEEDS_REVIEW),
        ({"domain_confidence": 50}, ModerationDisposition.NEEDS_REVIEW),
    ),
)
@pytest.mark.asyncio
async def test_moderation_decision_matrix(
    overrides: dict[str, object],
    expected: ModerationDisposition,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_payload(**overrides))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await moderate_reference(
            b"canonical",
            client=client,
            api_key=None,
            model="google/gemini-2.5-flash",
        )
    assert result.disposition is expected
    if expected is ModerationDisposition.APPROVED:
        assert result.material_regions[0].x == 100
        assert result.excluded_regions[0].width == 120
        assert result.localization_confidence == 93


@pytest.mark.asyncio
async def test_visible_product_label_becomes_bounded_suggested_name() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=response_payload(
                label_name="TPU Dream Grey Charm Purple",
                product_code="TPU-Z060",
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await moderate_reference(
            b"canonical",
            client=client,
            api_key=None,
            model="google/gemini-2.5-flash",
        )

    assert result.suggested_display_name == ("TPU Dream Grey Charm Purple TPU-Z060")


@pytest.mark.asyncio
async def test_provider_failures_and_invalid_schema_need_review() -> None:
    for payload in (
        b"not-json",
        b'{"choices": []}',
        b'{"choices":[{"message":{"content":"{}"}}]}',
    ):

        async def handler(
            request: httpx.Request,
            body: bytes = payload,
        ) -> httpx.Response:
            return httpx.Response(
                200,
                content=body,
                headers={"content-type": "application/json"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await moderate_reference(
                b"canonical",
                client=client,
                api_key=None,
                model="google/gemini-2.5-flash",
            )
        assert result.disposition is ModerationDisposition.NEEDS_REVIEW


@pytest.mark.asyncio
async def test_out_of_bounds_or_excessive_regions_need_review() -> None:
    invalid_regions = response_payload(
        material_regions=[{"x": 900, "y": 0, "width": 200, "height": 500}]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=invalid_regions)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await moderate_reference(
            b"canonical",
            client=client,
            api_key=None,
            model="google/gemini-2.5-flash",
        )
    assert result.disposition is ModerationDisposition.NEEDS_REVIEW
    assert result.material_regions == ()


@pytest.mark.asyncio
async def test_network_and_oversized_response_need_review() -> None:
    async def network_failure(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(network_failure)
    ) as client:
        result = await moderate_reference(
            b"canonical",
            client=client,
            api_key=None,
            model="google/gemini-2.5-flash",
        )
    assert result.disposition is ModerationDisposition.NEEDS_REVIEW

    async def oversized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * (33 * 1024),
            headers={"content-type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(oversized)) as client:
        result = await moderate_reference(
            b"canonical",
            client=client,
            api_key=None,
            model="google/gemini-2.5-flash",
        )
    assert result.disposition is ModerationDisposition.NEEDS_REVIEW


def test_payload_is_server_owned_and_contains_no_display_name() -> None:
    payload = build_moderation_payload(
        b"canonical",
        model="google/gemini-2.5-flash",
    )
    rendered = json.dumps(payload)
    assert "Injected user name" not in rendered
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["provider"]["require_parameters"] is True


@pytest.mark.parametrize(
    "value",
    ("", " " * 3, "x" * 41, "safe\u0000name", "safe\u202ename"),
)
def test_name_normalization_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_display_name(value)


def test_name_normalization_is_bounded_display_text() -> None:
    assert normalize_display_name("  Bronze\u00a0Satin  ") == "Bronze Satin"
