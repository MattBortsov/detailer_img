from __future__ import annotations

import base64
import hashlib
import io
import json
import warnings
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from PIL import Image

from car_wrap.config import EvalSettings
from car_wrap.eval.models import (
    EvaluationColor,
    FixtureMetadata,
    ImageGenerationRequest,
    ProviderError,
    ProviderErrorCode,
)
from car_wrap.eval.openrouter import (
    PROVIDER_RESPONSE_ENVELOPE_BYTES,
    generate_image,
)
from car_wrap.prompting import PROMPT_REVISION


def _png(*, width: int = 2, height: int = 2) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(12, 58, 102)).save(
        buffer,
        format="PNG",
    )
    return buffer.getvalue()


def _request(source: bytes | None = None) -> ImageGenerationRequest:
    source_bytes = _png() if source is None else source
    return ImageGenerationRequest(
        source_bytes=source_bytes,
        source_media_type="image/png",
        fixture=FixtureMetadata(
            case_id="car-front-light",
            byte_count=len(source_bytes),
            sha256=hashlib.sha256(source_bytes).hexdigest(),
            source_media_type="image/png",
        ),
        color=EvaluationColor(
            color_id="deep-blue",
            display_name="Deep Blue",
            rgb_hex="#123A66",
        ),
    )


def _response_payload(image_bytes: bytes | None = None) -> dict[str, object]:
    encoded = base64.b64encode(_png() if image_bytes is None else image_bytes)
    return {
        "data": [{"b64_json": encoded.decode("ascii")}],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
            "cost": 0.04,
        },
    }


async def _call(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    settings: EvalSettings | None = None,
) -> object:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        return await generate_image(
            _request(),
            client=client,
            settings=settings or EvalSettings(),
        )


@pytest.mark.asyncio
async def test_posts_one_configured_request_with_explicit_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        payload = json.loads(request.content)
        assert request.method == "POST"
        assert request.url == "https://openrouter.ai/api/v1/images"
        assert payload["model"] == "operator/model-v2"
        assert payload["prompt"]
        assert set(payload) == {"model", "prompt", "input_references"}
        assert payload["input_references"] == [
            {
                "type": "image_url",
                "image_url": {
                    "url": payload["input_references"][0]["image_url"]["url"]
                },
            }
        ]
        assert payload["input_references"][0]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )
        assert request.extensions["timeout"] == {
            "connect": 3.0,
            "read": 90.0,
            "write": 10.0,
            "pool": 2.0,
        }
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json; charset=utf-8",
                "x-request-id": "req_safe-123",
            },
            json=_response_payload(),
        )

    result = await _call(
        handler,
        settings=EvalSettings(
            openrouter_image_model="operator/model-v2",
            openrouter_connect_timeout_seconds=3.0,
            openrouter_read_timeout_seconds=90.0,
            openrouter_write_timeout_seconds=10.0,
            openrouter_pool_timeout_seconds=2.0,
        ),
    )

    assert len(calls) == 1
    assert result.image_bytes == _png()  # type: ignore[attr-defined]
    metadata = result.metadata  # type: ignore[attr-defined]
    assert metadata.model == "operator/model-v2"
    assert metadata.prompt_revision == PROMPT_REVISION
    assert metadata.output_bytes == len(_png())
    assert metadata.width == 2
    assert metadata.height == 2
    assert metadata.image_format == "png"
    assert metadata.provider.request_id == "req_safe-123"
    assert metadata.usage.input_tokens == 10
    assert metadata.usage.output_tokens == 20
    assert metadata.usage.total_tokens == 30
    assert str(metadata.usage.cost_usd) == "0.04"
    assert "source_bytes" not in repr(result)
    assert "image_bytes" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "content_type", "content", "code"),
    [
        (500, "application/json", b'{"secret":"raw-error-canary"}', "http_error"),
        (200, "text/html", b"<b>signed-url-canary</b>", "invalid_content_type"),
        (200, "application/json", b"{bad-json-canary", "invalid_json"),
        (200, "application/json", b'{"data":[]}', "invalid_response_shape"),
        (
            200,
            "application/json",
            b'{"data":[{"b64_json":"%%%invalid-base64-canary%%%"}]}',
            "invalid_base64",
        ),
        (
            200,
            "application/json",
            json.dumps(
                {
                    "data": [
                        {"b64_json": base64.b64encode(b"not-an-image-canary").decode()}
                    ]
                }
            ).encode(),
            "invalid_image",
        ),
    ],
)
async def test_malformed_responses_fail_once_with_fixed_safe_errors(
    status: int,
    content_type: str,
    content: bytes,
    code: str,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            headers={"content-type": content_type},
            content=content,
        )

    with pytest.raises(ProviderError) as caught:
        await _call(handler)

    assert calls == 1
    assert caught.value.code.value == code
    serialized = f"{caught.value!s} {caught.value!r}"
    for canary in (
        "raw-error-canary",
        "signed-url-canary",
        "bad-json-canary",
        "invalid-base64-canary",
        "not-an-image-canary",
    ):
        assert canary not in serialized


@pytest.mark.asyncio
async def test_encoded_and_decoded_byte_caps_are_enforced() -> None:
    encoded = base64.b64encode(_png()).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

    with pytest.raises(ProviderError) as encoded_error:
        await _call(
            handler,
            settings=EvalSettings(provider_max_output_bytes=8),
        )
    assert encoded_error.value.code is ProviderErrorCode.OUTPUT_TOO_LARGE

    padded = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 10).decode()

    def padded_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"b64_json": padded}]})

    with pytest.raises(ProviderError):
        await _call(
            padded_handler,
            settings=EvalSettings(provider_max_output_bytes=16),
        )


class _CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks
        self.yielded = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk


@pytest.mark.asyncio
async def test_content_length_rejects_before_streaming_response_body() -> None:
    stream = _CountingStream((b"must-not-be-read",))
    maximum = PROVIDER_RESPONSE_ENVELOPE_BYTES + 4

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-length": str(maximum + 1),
            },
            stream=stream,
        )

    with pytest.raises(ProviderError) as caught:
        await _call(
            handler,
            settings=EvalSettings(provider_max_output_bytes=1),
        )

    assert caught.value.code is ProviderErrorCode.OUTPUT_TOO_LARGE
    assert stream.yielded == 0


@pytest.mark.asyncio
async def test_chunked_response_aborts_when_bounded_json_envelope_is_crossed() -> None:
    stream = _CountingStream(
        (
            b"x" * 32_768,
            b"x" * 32_768,
            b"x" * 8,
            b"must-not-be-read",
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=stream,
        )

    with pytest.raises(ProviderError) as caught:
        await _call(
            handler,
            settings=EvalSettings(provider_max_output_bytes=1),
        )

    assert caught.value.code is ProviderErrorCode.OUTPUT_TOO_LARGE
    assert stream.yielded == 3


@pytest.mark.asyncio
async def test_usage_accepts_legacy_aliases_and_rejects_conflicts() -> None:
    payload = _response_payload()
    payload["usage"] = {
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
    }

    def legacy_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    result = await _call(legacy_handler)
    assert result.metadata.usage.input_tokens == 1  # type: ignore[attr-defined]
    assert result.metadata.usage.output_tokens == 2  # type: ignore[attr-defined]

    payload["usage"] = {"input_tokens": 1, "prompt_tokens": 2}
    with pytest.raises(ProviderError) as caught:
        await _call(legacy_handler)
    assert caught.value.code is ProviderErrorCode.INVALID_RESPONSE_SHAPE


@pytest.mark.asyncio
async def test_pixel_limit_and_decompression_warning_fail_before_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response_payload(_png(width=3, height=3)))

    with pytest.raises(ProviderError) as pixel_error:
        await _call(
            handler,
            settings=EvalSettings(provider_max_image_pixels=4),
        )
    assert pixel_error.value.code is ProviderErrorCode.INVALID_IMAGE

    original_open = Image.open

    def warning_open(*args: object, **kwargs: object) -> Image.Image:
        warnings.warn("canary-bomb", Image.DecompressionBombWarning, stacklevel=2)
        return original_open(*args, **kwargs)

    monkeypatch.setattr(Image, "open", warning_open)
    with pytest.raises(ProviderError) as bomb_error:
        await _call(handler)
    assert bomb_error.value.code is ProviderErrorCode.INVALID_IMAGE
    assert "canary-bomb" not in str(bomb_error.value)


@pytest.mark.asyncio
async def test_network_errors_and_live_missing_key_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "https://signed.example/private?signature=network-canary"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(canary, request=request)

    with pytest.raises(ProviderError) as caught:
        await _call(handler)
    assert caught.value.code is ProviderErrorCode.NETWORK_ERROR
    assert "network-canary" not in f"{caught.value!s} {caught.value!r}"

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    async with httpx.AsyncClient() as client:
        with pytest.raises(ProviderError) as missing:
            await generate_image(
                _request(),
                client=client,
                settings=EvalSettings(),
            )
    assert missing.value.code is ProviderErrorCode.MISSING_CREDENTIAL
