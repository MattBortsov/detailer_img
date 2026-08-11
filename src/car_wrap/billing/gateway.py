"""HMAC-authenticated client for the shared SeoSmith payment gateway."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from car_wrap.config import AppSettings

PAYMENT_GATEWAY_SOURCE = "car_wrap_bot"
SIGNATURE_HEADER = "X-Payment-Signature"
TIMESTAMP_HEADER = "X-Payment-Timestamp"
_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)
_SHORT_PAYMENT_PATH = re.compile(r"/pay/[A-Za-z0-9_-]{22}")


class PaymentActivationDenied(RuntimeError):
    """Payment movement is not explicitly authorized and configured."""

    def __init__(self) -> None:
        super().__init__("payment activation is unavailable")


class PaymentGatewayProtocolError(RuntimeError):
    """SeoSmith rejected a request or returned an unusable response."""


class PaymentGatewayRequestNotSent(PaymentGatewayProtocolError):
    """The request failed before it could reach SeoSmith."""


class PaymentGatewayOutcomeAmbiguous(PaymentGatewayProtocolError):
    """The request may have reached SeoSmith and must be reconciled."""


@dataclass(frozen=True, slots=True)
class GatewayCheckout:
    invoice_id: int
    redirect_url: str


@dataclass(frozen=True, slots=True)
class GatewayRecurring:
    invoice_id: int
    status: str


def _is_allowed_checkout_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port
        ):
            return False
    except ValueError:
        return False
    if parsed.hostname == "auth.robokassa.ru":
        return parsed.path.startswith("/Merchant/")
    return bool(
        parsed.hostname == "seo-smith.ru"
        and _SHORT_PAYMENT_PATH.fullmatch(parsed.path)
        and not parsed.query
        and not parsed.fragment
    )


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def message_signature(secret: str, timestamp: str, body: bytes) -> str:
    message = timestamp.encode("ascii") + b"." + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


class PaymentGatewayClient:
    """Create bot-owned invoices without exposing Robokassa credentials."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    @property
    def production_available(self) -> bool:
        secret = self._settings.payment_gateway_secret
        return bool(
            self._settings.payment_gateway_base_url
            and secret
            and secret.get_secret_value()
        )

    def ensure_available(self) -> None:
        if not self.production_available:
            raise PaymentActivationDenied

    def _secret(self) -> str:
        self.ensure_available()
        secret = self._settings.payment_gateway_secret
        if secret is None or not secret.get_secret_value():
            raise PaymentActivationDenied
        return secret.get_secret_value()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = canonical_json(payload)
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: message_signature(self._secret(), timestamp, body),
        }
        base_url = self._settings.payment_gateway_base_url
        if base_url is None:
            raise PaymentActivationDenied
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{base_url.rstrip('/')}/{path.lstrip('/')}",
                    content=body,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise PaymentGatewayRequestNotSent from None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                raise PaymentGatewayOutcomeAmbiguous from None
            raise PaymentGatewayProtocolError(
                "payment gateway rejected request"
            ) from None
        except (httpx.HTTPError, ValueError):
            raise PaymentGatewayOutcomeAmbiguous from None
        if not isinstance(data, dict):
            raise PaymentGatewayOutcomeAmbiguous("invalid payment gateway response")
        return data

    async def create_checkout(
        self,
        *,
        external_order_id: str,
        amount_kopecks: int,
        description: str,
        recurring: bool,
    ) -> GatewayCheckout:
        data = await self._post(
            "checkout",
            {
                "amount_kopecks": amount_kopecks,
                "description": description,
                "external_order_id": external_order_id,
                "recurring": recurring,
                "source": PAYMENT_GATEWAY_SOURCE,
            },
        )
        invoice_id = data.get("invoice_id")
        redirect_url = data.get("redirect_url")
        if (
            not isinstance(invoice_id, int)
            or isinstance(invoice_id, bool)
            or invoice_id <= 0
            or not isinstance(redirect_url, str)
            or not _is_allowed_checkout_url(redirect_url)
        ):
            raise PaymentGatewayOutcomeAmbiguous("invalid checkout response")
        return GatewayCheckout(invoice_id=invoice_id, redirect_url=redirect_url)

    async def submit_recurring(
        self,
        *,
        external_order_id: str,
        previous_invoice_id: int,
        amount_kopecks: int,
        description: str,
    ) -> GatewayRecurring:
        data = await self._post(
            "recurring",
            {
                "amount_kopecks": amount_kopecks,
                "description": description,
                "external_order_id": external_order_id,
                "previous_invoice_id": previous_invoice_id,
                "recurring": True,
                "source": PAYMENT_GATEWAY_SOURCE,
            },
        )
        invoice_id = data.get("invoice_id")
        status = data.get("status")
        if (
            not isinstance(invoice_id, int)
            or isinstance(invoice_id, bool)
            or invoice_id <= 0
            or status
            not in {"submitting", "submitted", "ambiguous", "paid", "delivered"}
        ):
            raise PaymentGatewayOutcomeAmbiguous("invalid recurring response")
        return GatewayRecurring(invoice_id=invoice_id, status=str(status))

    def verify_callback(
        self,
        *,
        timestamp: str | None,
        signature: str | None,
        body: bytes,
        now: int | None = None,
    ) -> bool:
        if not timestamp or not signature:
            return False
        try:
            request_time = int(timestamp)
        except ValueError:
            return False
        current_time = int(time.time()) if now is None else now
        if (
            abs(current_time - request_time)
            > self._settings.payment_gateway_max_clock_skew_seconds
        ):
            return False
        try:
            expected = message_signature(self._secret(), timestamp, body)
        except PaymentActivationDenied:
            return False
        return hmac.compare_digest(signature.lower(), expected.lower())
