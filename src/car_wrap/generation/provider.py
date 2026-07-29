"""Single-attempt OpenRouter Images runtime adapter."""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

from car_wrap.config import AppSettings, EvalSettings
from car_wrap.eval.models import ProviderError
from car_wrap.eval.openrouter import (
    OPENROUTER_IMAGES_URL,
    _read_bounded_response,
    decode_and_validate_response,
)
from car_wrap.jobs.contracts import ProviderReceipt


class ProviderFailureKind(StrEnum):
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"
    INVALID_RESPONSE = "invalid_response"
    AMBIGUOUS = "ambiguous"


class ProviderFailure(RuntimeError):
    def __init__(self, kind: ProviderFailureKind) -> None:
        self.kind = kind
        super().__init__(kind.value)


@dataclass(frozen=True, slots=True)
class ProviderImage:
    data: bytes = field(repr=False)
    receipt: ProviderReceipt


SafeRetryHook = Callable[[], Awaitable[None]]


class OpenRouterImagesProvider:
    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: AppSettings,
    ) -> None:
        self._client = client
        self._settings = settings

    async def generate(
        self,
        payload: Mapping[str, Any],
        *,
        on_safe_retry: SafeRetryHook | None = None,
    ) -> ProviderImage:
        """Send one uploaded request, with one connect-only retry allowance."""

        api_key = os.environ.get("OPENROUTER_API_KEY")
        is_mock = isinstance(
            getattr(self._client, "_transport", None),
            httpx.MockTransport,
        )
        if not api_key and not is_mock:
            raise ProviderFailure(ProviderFailureKind.UNAVAILABLE)
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        timeout = httpx.Timeout(
            connect=self._settings.openrouter_connect_timeout_seconds,
            read=self._settings.openrouter_read_timeout_seconds,
            write=self._settings.openrouter_write_timeout_seconds,
            pool=self._settings.openrouter_pool_timeout_seconds,
        )
        safe_failures = (httpx.PoolTimeout, httpx.ConnectTimeout, httpx.ConnectError)
        started = time.monotonic_ns()
        for connection_attempt in range(2):
            try:
                async with self._client.stream(
                    "POST",
                    OPENROUTER_IMAGES_URL,
                    headers=headers,
                    json=dict(payload),
                    timeout=timeout,
                ) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        raise ProviderFailure(ProviderFailureKind.UNAVAILABLE)
                    if response.status_code >= 400:
                        raise ProviderFailure(ProviderFailureKind.REJECTED)
                    body = await _read_bounded_response(
                        response,
                        settings=self._eval_settings(payload),
                    )
                latency_ms = max(
                    0,
                    (time.monotonic_ns() - started) // 1_000_000,
                )
                generated = decode_and_validate_response(
                    response,
                    body=body,
                    settings=self._eval_settings(payload),
                    latency_ms=latency_ms,
                )
                metadata = generated.metadata
                status_code = metadata.provider.status_code
                if status_code is None:
                    raise ProviderFailure(ProviderFailureKind.INVALID_RESPONSE)
                return ProviderImage(
                    data=generated.image_bytes,
                    receipt=ProviderReceipt(
                        provider_name=metadata.provider.provider,
                        request_id=metadata.provider.request_id,
                        status_code=status_code,
                        latency_ms=metadata.latency_ms,
                        input_tokens=metadata.usage.input_tokens,
                        output_tokens=metadata.usage.output_tokens,
                        total_tokens=metadata.usage.total_tokens,
                        cost_usd=metadata.usage.cost_usd,
                        output_byte_count=metadata.output_bytes,
                        output_width=metadata.width,
                        output_height=metadata.height,
                        output_format=metadata.image_format,
                        output_sha256=hashlib.sha256(generated.image_bytes).hexdigest(),
                    ),
                )
            except safe_failures:
                if connection_attempt == 0:
                    if on_safe_retry is not None:
                        await on_safe_retry()
                    continue
                raise ProviderFailure(ProviderFailureKind.UNAVAILABLE) from None
            except ProviderFailure:
                raise
            except ProviderError:
                raise ProviderFailure(ProviderFailureKind.INVALID_RESPONSE) from None
            except httpx.RequestError:
                raise ProviderFailure(ProviderFailureKind.AMBIGUOUS) from None
        raise AssertionError("unreachable provider retry state")

    def _eval_settings(self, payload: Mapping[str, Any]) -> EvalSettings:
        model = payload.get("model")
        if not isinstance(model, str):
            raise ProviderFailure(ProviderFailureKind.INVALID_RESPONSE)
        return EvalSettings(
            openrouter_image_model=model,
            provider_max_output_bytes=self._settings.provider_max_output_bytes,
            provider_max_image_width=self._settings.provider_max_image_side_px,
            provider_max_image_height=self._settings.provider_max_image_side_px,
            provider_max_image_pixels=self._settings.provider_max_image_pixels,
            openrouter_connect_timeout_seconds=(
                self._settings.openrouter_connect_timeout_seconds
            ),
            openrouter_read_timeout_seconds=(
                self._settings.openrouter_read_timeout_seconds
            ),
            openrouter_write_timeout_seconds=(
                self._settings.openrouter_write_timeout_seconds
            ),
            openrouter_pool_timeout_seconds=(
                self._settings.openrouter_pool_timeout_seconds
            ),
        )
