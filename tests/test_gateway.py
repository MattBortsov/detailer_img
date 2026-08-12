"""Offline checks for the shared SeoSmith payment-gateway contract."""

from __future__ import annotations

import json
import time
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Self
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from car_wrap.api.routes.payments import MAX_RESULT_BYTES, router
from car_wrap.billing.catalog import get_payable_product
from car_wrap.billing.gateway import (
    PAYMENT_GATEWAY_SOURCE,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    PaymentGatewayClient,
    PaymentGatewayOutcomeAmbiguous,
    _is_allowed_checkout_url,
    canonical_json,
    message_signature,
)
from car_wrap.billing.payments import PaymentConfirmationError, PaymentService
from car_wrap.config import AppSettings
from car_wrap.db.models import (
    AllowanceBalance,
    BillingOrder,
    IntroRecurringChargeSource,
    RobokassaPayment,
    Subscription,
)


def _settings(**overrides: object) -> AppSettings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://user:pass@localhost/car_wrap",
        "bot_token": "test-token",
        "bot_username": "CarWrapBot",
        "mini_app_url": "https://bot.example/app",
        "payment_gateway_base_url": "https://seo-smith.ru/api/payments/gateway",
        "payment_gateway_secret": "s" * 32,
    }
    values.update(overrides)
    return AppSettings.model_validate(values)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://auth.robokassa.ru/Merchant/Index.aspx?x=1", True),
        ("https://seo-smith.ru/pay/AbCdEfGhIjKlMnOpQrStUv", True),
        ("https://seo-smith.ru/pay/too-short", False),
        ("https://seo-smith.ru.evil.test/pay/AbCdEfGhIjKlMnOpQrStUv", False),
        ("http://seo-smith.ru/pay/AbCdEfGhIjKlMnOpQrStUv", False),
    ],
)
def test_checkout_url_allowlist(url: str, expected: bool) -> None:
    assert _is_allowed_checkout_url(url) is expected


@pytest.mark.asyncio
async def test_checkout_request_is_hmac_signed_and_returns_robokassa_url() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = bytes(request.content)
        captured["timestamp"] = request.headers[TIMESTAMP_HEADER]
        captured["signature"] = request.headers[SIGNATURE_HEADER]
        return httpx.Response(
            200,
            json={
                "invoice_id": 42,
                "redirect_url": "https://seo-smith.ru/pay/AbCdEfGhIjKlMnOpQrStUv",
            },
        )

    client = PaymentGatewayClient(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    result = await client.create_checkout(
        external_order_id="order-1",
        amount_kopecks=49_900,
        description="Car Wrap: plus",
        recurring=True,
    )

    assert captured["url"].endswith("/api/payments/gateway/checkout")
    payload = json.loads(captured["body"])
    assert payload["source"] == PAYMENT_GATEWAY_SOURCE
    assert payload["amount_kopecks"] == 49_900
    assert captured["signature"] == message_signature(
        "s" * 32,
        captured["timestamp"],
        captured["body"],
    )
    assert result.invoice_id == 42


@pytest.mark.asyncio
async def test_recurring_uses_parent_and_waits_for_result() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"invoice_id": 43, "status": "submitted"})

    client = PaymentGatewayClient(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    result = await client.submit_recurring(
        external_order_id="order-2",
        previous_invoice_id=42,
        amount_kopecks=49_900,
        description="Car Wrap renewal: plus",
    )

    assert captured["previous_invoice_id"] == 42
    assert captured["recurring"] is True
    assert result.invoice_id == 43
    assert result.status == "submitted"


@pytest.mark.asyncio
async def test_gateway_server_error_is_ambiguous() -> None:
    client = PaymentGatewayClient(
        _settings(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(503, text="unavailable")
        ),
    )
    with pytest.raises(PaymentGatewayOutcomeAmbiguous):
        await client.create_checkout(
            external_order_id="order-1",
            amount_kopecks=49_900,
            description="Car Wrap: plus",
            recurring=True,
        )


@pytest.mark.asyncio
async def test_malformed_success_response_is_ambiguous() -> None:
    client = PaymentGatewayClient(
        _settings(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"invoice_id": 42})
        ),
    )
    with pytest.raises(PaymentGatewayOutcomeAmbiguous):
        await client.create_checkout(
            external_order_id="order-1",
            amount_kopecks=49_900,
            description="Car Wrap: plus",
            recurring=True,
        )


def test_callback_signature_is_fresh_and_constant_time_checked() -> None:
    client = PaymentGatewayClient(_settings())
    body = canonical_json(
        {
            "amount_kopecks": 49_900,
            "external_order_id": "order-1",
            "invoice_id": 42,
            "source": PAYMENT_GATEWAY_SOURCE,
            "status": "paid",
        }
    )
    timestamp = str(int(time.time()))
    signature = message_signature("s" * 32, timestamp, body)

    assert client.verify_callback(
        timestamp=timestamp,
        signature=signature.upper(),
        body=body,
    )
    assert not client.verify_callback(
        timestamp=timestamp,
        signature="0" * 64,
        body=body,
    )
    assert not client.verify_callback(
        timestamp=str(int(timestamp) - 301),
        signature=signature,
        body=body,
    )


def test_empty_gateway_secret_keeps_payment_gate_closed() -> None:
    client = PaymentGatewayClient(_settings(payment_gateway_secret=None))
    assert not client.production_available


class _Transaction(AbstractAsyncContextManager[None]):
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class _Session(AbstractAsyncContextManager["_Session"]):
    def __init__(self, values: list[object]) -> None:
        self._values = values

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> _Transaction:
        return _Transaction()

    async def scalar(self, _statement: object) -> object:
        return self._values.pop(0)


class _Repository:
    async def lock_account(self, _session: object, *, user_id: int) -> None:
        assert user_id == 123


class _GrantSession:
    def __init__(self, scalar_values: list[object] | None = None) -> None:
        self.added: list[object] = []
        self.scalar_values = scalar_values or []

    def add(self, entity: object) -> None:
        if isinstance(entity, (AllowanceBalance, Subscription)) and entity.id is None:
            entity.id = uuid4()
        self.added.append(entity)

    async def flush(self) -> None:
        return None

    async def scalar(self, _statement: object) -> object:
        return self.scalar_values.pop(0)


class _GrantRepository:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def balance(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def append_ledger_entry(self, *_args: object, **values: Any) -> None:
        self.entries.append(values)


@pytest.mark.asyncio
async def test_initial_monthly_result_persists_parent_invoice_and_grants() -> None:
    order = BillingOrder(
        id=uuid4(),
        telegram_user_id=123,
        product_id="plus",
        amount_kopecks=49_900,
        intro_number=None,
        currency="RUB",
        status="confirmed",
        idempotency_key="parent-test",
        recurring_consent_at=datetime.now(UTC),
    )
    payment = RobokassaPayment(
        id=uuid4(),
        order_id=order.id,
        invoice_id=42,
        previous_invoice_id=None,
        status="confirmed",
    )
    repository = _GrantRepository()
    service = PaymentService(
        lambda: None,  # type: ignore[arg-type,return-value]
        PaymentGatewayClient(_settings()),
        repository=repository,  # type: ignore[arg-type]
    )
    session = _GrantSession()

    await service._grant_purchase(
        session,  # type: ignore[arg-type]
        order,
        payment,
        get_payable_product("plus"),
        datetime.now(UTC),
    )

    subscriptions = [item for item in session.added if isinstance(item, Subscription)]
    assert len(subscriptions) == 1
    assert subscriptions[0].robokassa_parent_invoice_id == 42
    assert len(repository.entries) == 2


@pytest.mark.asyncio
async def test_other_card_replaces_saved_intro_source_and_grants() -> None:
    now = datetime.now(UTC)
    order = BillingOrder(
        id=uuid4(),
        telegram_user_id=123,
        product_id="intro_25",
        amount_kopecks=2_500,
        intro_number=4,
        currency="RUB",
        status="confirmed",
        idempotency_key="other-card",
    )
    payment = RobokassaPayment(
        id=uuid4(),
        order_id=order.id,
        invoice_id=84,
        previous_invoice_id=None,
        status="confirmed",
    )
    previous_source = IntroRecurringChargeSource(
        id=uuid4(),
        telegram_user_id=123,
        source_order_id=uuid4(),
        parent_invoice_id=42,
        amount_kopecks=2_500,
        status="active",
    )
    repository = _GrantRepository()
    service = PaymentService(
        lambda: None,  # type: ignore[arg-type,return-value]
        PaymentGatewayClient(_settings()),
        repository=repository,  # type: ignore[arg-type]
    )
    session = _GrantSession([previous_source])

    await service._grant_purchase(
        session,  # type: ignore[arg-type]
        order,
        payment,
        get_payable_product("intro_25"),
        now,
    )

    sources = [
        item for item in session.added if isinstance(item, IntroRecurringChargeSource)
    ]
    assert previous_source.status == "cancelled"
    assert previous_source.cancelled_at == now
    assert len(sources) == 1
    assert sources[0].parent_invoice_id == 84


@pytest.mark.asyncio
async def test_valid_gateway_result_grants_once_and_duplicate_is_noop() -> None:
    order_id = uuid4()
    order = BillingOrder(
        id=order_id,
        telegram_user_id=123,
        product_id="plus",
        amount_kopecks=49_900,
        intro_number=None,
        currency="RUB",
        status="pending",
        idempotency_key="result-test",
        recurring_consent_at=None,
    )
    payment = RobokassaPayment(
        id=uuid4(),
        order_id=order_id,
        invoice_id=None,
        previous_invoice_id=None,
        status="initializing",
    )
    session = _Session([payment, order, payment, order])
    service = PaymentService(
        lambda: session,  # type: ignore[arg-type]
        PaymentGatewayClient(_settings()),
        repository=_Repository(),  # type: ignore[arg-type]
    )
    service._grant_purchase = AsyncMock()  # type: ignore[method-assign]

    credited = await service.confirm_result(
        external_order_id=str(order_id),
        invoice_id=42,
        amount_kopecks=49_900,
    )
    duplicate = await service.confirm_result(
        external_order_id=str(order_id),
        invoice_id=42,
        amount_kopecks=49_900,
    )

    assert credited == 123
    assert duplicate is None
    assert order.status == "confirmed"
    assert payment.status == "confirmed"
    assert payment.invoice_id == 42
    service._grant_purchase.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_late_gateway_response_does_not_undo_early_confirmation() -> None:
    order_id = uuid4()
    payment = RobokassaPayment(
        id=uuid4(),
        order_id=order_id,
        invoice_id=42,
        previous_invoice_id=41,
        status="confirmed",
    )
    service = PaymentService(
        lambda: _Session([payment]),  # type: ignore[arg-type]
        PaymentGatewayClient(_settings()),
    )

    await service._record_gateway_invoice(
        order_id=order_id,
        invoice_id=42,
        status="submitted",
    )

    assert payment.status == "confirmed"


class _RouteService:
    def __init__(self, *, reject: bool = False, valid_signature: bool = True) -> None:
        self.reject = reject
        self.valid_signature = valid_signature
        self.calls: list[dict[str, Any]] = []

    def verify_gateway_callback(self, **_values: Any) -> bool:
        return self.valid_signature

    async def confirm_result(self, **values: Any) -> int | None:
        self.calls.append(values)
        if self.reject:
            raise PaymentConfirmationError
        return 123


def _route_client(service: _RouteService) -> TestClient:
    app = FastAPI()
    app.state.payment_service = service
    app.state.telegram_bot = None
    app.state.settings = SimpleNamespace(mini_app_url="https://bot.example/app")
    app.include_router(router)
    return TestClient(app)


def test_result_route_accepts_signed_json_and_rejects_oversize() -> None:
    service = _RouteService()
    client = _route_client(service)
    payload = {
        "amount_kopecks": 49_900,
        "external_order_id": str(uuid4()),
        "invoice_id": 42,
        "source": PAYMENT_GATEWAY_SOURCE,
        "status": "paid",
    }
    response = client.post(
        "/api/v1/payments/gateway/result",
        content=canonical_json(payload),
        headers={TIMESTAMP_HEADER: "1", SIGNATURE_HEADER: "signature"},
    )
    oversize = client.post(
        "/api/v1/payments/gateway/result",
        content=b"x" * (MAX_RESULT_BYTES + 1),
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert service.calls[0]["invoice_id"] == 42
    assert oversize.status_code == 400


def test_result_route_rejects_invalid_hmac_before_grant() -> None:
    service = _RouteService(valid_signature=False)
    client = _route_client(service)

    response = client.post(
        "/api/v1/payments/gateway/result",
        content=canonical_json(
            {
                "amount_kopecks": 49_900,
                "external_order_id": str(uuid4()),
                "invoice_id": 42,
                "source": PAYMENT_GATEWAY_SOURCE,
                "status": "paid",
            }
        ),
    )

    assert response.status_code == 401
    assert not service.calls
