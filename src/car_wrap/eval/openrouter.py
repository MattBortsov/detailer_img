"""Single-attempt, bounded OpenRouter Images evaluation adapter."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from car_wrap.config import EvalSettings
from car_wrap.eval.image_validation import ImageValidationError, validate_image_bytes
from car_wrap.eval.models import (
    GeneratedImage,
    GeneratedImageMetadata,
    ImageGenerationRequest,
    ProviderError,
    ProviderErrorCode,
    ProviderMetadata,
    ProviderUsage,
)
from car_wrap.prompting import PROMPT_REVISION, build_recolor_prompt

OPENROUTER_IMAGES_URL = "https://openrouter.ai/api/v1/images"
PROVIDER_RESPONSE_ENVELOPE_BYTES = 64 * 1024
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _fail(
    code: ProviderErrorCode,
    *,
    status_code: int | None = None,
) -> ProviderError:
    return ProviderError(code, status_code=status_code)


def _safe_usage(value: object) -> ProviderUsage:
    if value is None:
        return ProviderUsage()
    if not isinstance(value, dict):
        raise _fail(ProviderErrorCode.INVALID_RESPONSE_SHAPE)

    alias_pairs = (
        ("input_tokens", "prompt_tokens"),
        ("output_tokens", "completion_tokens"),
        ("total_tokens", None),
    )
    integers: dict[str, int | None] = {}
    for name, documented_alias in alias_pairs:
        item = value.get(name)
        if documented_alias is not None:
            alias_item = value.get(documented_alias)
            if item is not None and alias_item is not None and item != alias_item:
                raise _fail(ProviderErrorCode.INVALID_RESPONSE_SHAPE)
            if alias_item is not None:
                item = alias_item
        if item is not None and (type(item) is not int or item < 0):
            raise _fail(ProviderErrorCode.INVALID_RESPONSE_SHAPE)
        integers[name] = item

    raw_cost = value.get("cost", value.get("cost_usd"))
    cost: Decimal | None = None
    if raw_cost is not None:
        if isinstance(raw_cost, bool) or not isinstance(raw_cost, (int, float, str)):
            raise _fail(ProviderErrorCode.INVALID_RESPONSE_SHAPE)
        try:
            cost = Decimal(str(raw_cost))
        except InvalidOperation:
            raise _fail(ProviderErrorCode.INVALID_RESPONSE_SHAPE) from None
        if not cost.is_finite() or cost < 0:
            raise _fail(ProviderErrorCode.INVALID_RESPONSE_SHAPE)
    return ProviderUsage(**integers, cost_usd=cost)


def _validate_image(
    data: bytes,
    *,
    settings: EvalSettings,
) -> tuple[int, int, str]:
    try:
        validated = validate_image_bytes(
            data,
            max_width=settings.provider_max_image_width,
            max_height=settings.provider_max_image_height,
            max_pixels=settings.provider_max_image_pixels,
        )
    except ImageValidationError:
        raise _fail(ProviderErrorCode.INVALID_IMAGE) from None
    return validated.width, validated.height, validated.image_format


def _validate_response_headers(response: httpx.Response) -> None:
    if not response.is_success:
        raise _fail(
            ProviderErrorCode.HTTP_ERROR,
            status_code=response.status_code,
        )
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type != "application/json":
        raise _fail(ProviderErrorCode.INVALID_CONTENT_TYPE)


def _maximum_response_bytes(settings: EvalSettings) -> int:
    max_encoded = 4 * ((settings.provider_max_output_bytes + 2) // 3)
    return max_encoded + PROVIDER_RESPONSE_ENVELOPE_BYTES


async def _read_bounded_response(
    response: httpx.Response,
    *,
    settings: EvalSettings,
) -> bytes:
    """Read a streamed response into a strictly bounded JSON envelope."""

    maximum = _maximum_response_bytes(settings)
    content_length = response.headers.get("content-length")
    if content_length is not None:
        if not content_length.isascii() or not content_length.isdecimal():
            raise _fail(ProviderErrorCode.INVALID_RESPONSE_SHAPE)
        if int(content_length) > maximum:
            raise _fail(ProviderErrorCode.OUTPUT_TOO_LARGE)

    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > maximum:
            raise _fail(ProviderErrorCode.OUTPUT_TOO_LARGE)
        body.extend(chunk)
    return bytes(body)


def decode_and_validate_response(
    response: httpx.Response,
    *,
    body: bytes,
    settings: EvalSettings,
    latency_ms: int,
) -> GeneratedImage:
    """Validate one provider response before exposing any image bytes."""

    _validate_response_headers(response)
    try:
        payload: Any = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise _fail(ProviderErrorCode.INVALID_JSON) from None
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("data"), list)
        or len(payload["data"]) != 1
        or not isinstance(payload["data"][0], dict)
        or not isinstance(payload["data"][0].get("b64_json"), str)
    ):
        raise _fail(ProviderErrorCode.INVALID_RESPONSE_SHAPE)
    encoded = payload["data"][0]["b64_json"]
    max_encoded = 4 * ((settings.provider_max_output_bytes + 2) // 3)
    if len(encoded) > max_encoded:
        raise _fail(ProviderErrorCode.OUTPUT_TOO_LARGE)
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise _fail(ProviderErrorCode.INVALID_BASE64) from None
    if len(image_bytes) > settings.provider_max_output_bytes:
        raise _fail(ProviderErrorCode.OUTPUT_TOO_LARGE)
    width, height, image_format = _validate_image(image_bytes, settings=settings)

    raw_request_id = response.headers.get("x-request-id")
    request_id = (
        raw_request_id
        if raw_request_id is not None and _REQUEST_ID.fullmatch(raw_request_id)
        else None
    )
    metadata = GeneratedImageMetadata(
        model=settings.openrouter_image_model,
        prompt_revision=PROMPT_REVISION,
        latency_ms=latency_ms,
        output_bytes=len(image_bytes),
        width=width,
        height=height,
        image_format=image_format,
        provider=ProviderMetadata(
            provider="openrouter",
            status_code=response.status_code,
            request_id=request_id,
        ),
        usage=_safe_usage(payload.get("usage")),
    )
    return GeneratedImage(image_bytes=image_bytes, metadata=metadata)


def _uses_mock_transport(client: httpx.AsyncClient) -> bool:
    return isinstance(getattr(client, "_transport", None), httpx.MockTransport)


async def generate_image(
    request: ImageGenerationRequest,
    *,
    client: httpx.AsyncClient,
    settings: EvalSettings,
) -> GeneratedImage:
    """Make exactly one Images API request and validate its in-memory result."""

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key and not _uses_mock_transport(client):
        raise _fail(ProviderErrorCode.MISSING_CREDENTIAL)
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    source_data_url = (
        f"data:{request.source_media_type};base64,"
        f"{base64.b64encode(request.source_bytes).decode('ascii')}"
    )
    payload = {
        "model": settings.openrouter_image_model,
        "prompt": build_recolor_prompt(request.color),
        "input_references": [
            {
                "type": "image_url",
                "image_url": {"url": source_data_url},
            }
        ],
    }
    timeout = httpx.Timeout(
        connect=settings.openrouter_connect_timeout_seconds,
        read=settings.openrouter_read_timeout_seconds,
        write=settings.openrouter_write_timeout_seconds,
        pool=settings.openrouter_pool_timeout_seconds,
    )
    started = time.monotonic_ns()
    try:
        async with client.stream(
            "POST",
            OPENROUTER_IMAGES_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
        ) as response:
            _validate_response_headers(response)
            body = await _read_bounded_response(response, settings=settings)
    except httpx.RequestError:
        raise _fail(ProviderErrorCode.NETWORK_ERROR) from None
    latency_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
    return decode_and_validate_response(
        response,
        body=body,
        settings=settings,
        latency_ms=latency_ms,
    )
