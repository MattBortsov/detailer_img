"""Runtime OpenRouter transport and ambiguity contracts."""

from __future__ import annotations

import base64
from io import BytesIO

import httpx
import pytest
from PIL import Image

from car_wrap.config import AppSettings
from car_wrap.generation.provider import (
    OpenRouterImagesProvider,
    ProviderFailure,
    ProviderFailureKind,
)


def _settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "database_url": "postgresql+psycopg://user:pass@db/test",
            "bot_token": "token",
            "bot_username": "CarWrapBot",
            "mini_app_url": "https://wrap.example.com/app",
        }
    )


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 24), color=(30, 80, 140)).save(buffer, format="PNG")
    return buffer.getvalue()


def _payload() -> dict[str, object]:
    return {
        "model": "x-ai/grok-imagine-image-quality",
        "prompt": "server-owned",
        "n": 1,
        "resolution": "1K",
        "input_references": [],
    }


@pytest.mark.asyncio
async def test_returns_validated_image_and_allowlisted_receipt() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/api/v1/images"
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-request-id": "req-safe",
            },
            json={
                "data": [{"b64_json": base64.b64encode(_png()).decode()}],
                "usage": {"cost": 0.06},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenRouterImagesProvider(client, _settings()).generate(
            _payload()
        )

    assert calls == 1
    assert result.data == _png()
    assert "data=" not in repr(result)
    assert result.receipt.request_id == "req-safe"
    assert str(result.receipt.cost_usd) == "0.06"


@pytest.mark.asyncio
async def test_retries_once_only_for_safe_connect_failure() -> None:
    calls = 0
    retry_hooks = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("secret-canary", request=request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"data": [{"b64_json": base64.b64encode(_png()).decode()}]},
        )

    async def on_retry() -> None:
        nonlocal retry_hooks
        retry_hooks += 1

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await OpenRouterImagesProvider(client, _settings()).generate(
            _payload(),
            on_safe_retry=on_retry,
        )
    assert calls == 2
    assert retry_hooks == 1


@pytest.mark.asyncio
async def test_read_failure_is_ambiguous_and_never_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("provider-secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderFailure) as caught:
            await OpenRouterImagesProvider(client, _settings()).generate(_payload())

    assert calls == 1
    assert caught.value.kind is ProviderFailureKind.AMBIGUOUS
    assert "provider-secret" not in repr(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, ProviderFailureKind.REJECTED),
        (429, ProviderFailureKind.UNAVAILABLE),
        (503, ProviderFailureKind.UNAVAILABLE),
    ],
)
async def test_http_failures_are_not_retried(
    status: int,
    kind: ProviderFailureKind,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, content=b"provider-body-secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderFailure) as caught:
            await OpenRouterImagesProvider(client, _settings()).generate(_payload())
    assert calls == 1
    assert caught.value.kind is kind
    assert "provider-body-secret" not in repr(caught.value)
