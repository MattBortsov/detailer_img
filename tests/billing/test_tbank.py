"""T-Bank protocol and release-gate contracts."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from car_wrap.billing.tbank import (
    PaymentActivationDenied,
    TBankClient,
    TBankInitRejected,
    TBankOutcomeAmbiguous,
    TBankRequestNotSent,
    canonical_token,
    verify_notification_token,
)
from car_wrap.config import AppSettings


def settings(**overrides: object) -> AppSettings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://user:pass@db/test",
        "bot_token": "bot-token",
        "bot_username": "CarWrapBot",
        "mini_app_url": "https://wrap.example.com/app",
        "tbank_terminal_key": "terminal-canary",
        "tbank_password": "password-canary",
        "tbank_notification_url": "https://wrap.example.com/api/v1/payments/tbank/webhook",
        "tbank_success_url": "https://wrap.example.com/app/payment/success",
        "tbank_fail_url": "https://wrap.example.com/app/payment/fail",
    }
    values.update(overrides)
    return AppSettings.model_validate(values)


def test_canonical_token_excludes_nested_values_and_is_constant_time_verified() -> None:
    payload = {
        "TerminalKey": "terminal-canary",
        "OrderId": "order-uuid",
        "Amount": 2500,
        "Success": True,
        "DATA": {"untrusted": "nested"},
    }
    token = canonical_token(payload, SecretStr("password-canary"))

    assert token == canonical_token(
        {**payload, "Token": "ignore"}, SecretStr("password-canary")
    )
    assert verify_notification_token(
        {**payload, "Token": token}, SecretStr("password-canary")
    )
    assert not verify_notification_token(
        {**payload, "Token": "0" * 64}, SecretStr("password-canary")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"payments_production_enabled": True},
        {"payments_production_enabled": True, "payments_owner_approved": True},
    ],
)
async def test_init_and_charge_fail_closed_without_complete_approved_predicate(
    overrides: dict[str, object],
) -> None:
    client = TBankClient(
        settings(**overrides), transport=httpx.MockTransport(lambda _: None)
    )

    with pytest.raises(PaymentActivationDenied):
        await client.init_payment(
            order_id="payment-order-uuid",
            amount_kopecks=2500,
            description="Car Wrap generation",
            customer_key="1001",
            recurrent=False,
        )
    with pytest.raises(PaymentActivationDenied):
        await client.charge(payment_id="payment-id", rebill_id="rebill-id")


@pytest.mark.asyncio
async def test_injected_approval_allows_fake_init_and_charge() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/Init"):
            return httpx.Response(
                200,
                json={
                    "Success": True,
                    "PaymentId": "p-1",
                    "PaymentURL": "https://pay.example",
                },
            )
        return httpx.Response(200, json={"Success": True, "PaymentId": "p-1"})

    client = TBankClient(
        settings(),
        transport=httpx.MockTransport(handler),
        production_approved=lambda: True,
    )
    initialized = await client.init_payment(
        order_id="payment-order-uuid",
        amount_kopecks=2500,
        description="Car Wrap generation",
        customer_key="1001",
        recurrent=True,
    )
    charged = await client.charge(payment_id="p-1", rebill_id="rebill-id")

    assert initialized.payment_id == "p-1"
    assert initialized.payment_url == "https://pay.example"
    assert charged.payment_id == "p-1"
    assert {request.url.path for request in calls} == {"/v2/Init", "/v2/Charge"}


@pytest.mark.asyncio
async def test_renewal_init_uses_the_tbank_mit_recurrent_parameters() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"Success": True, "PaymentId": "p-2"})

    client = TBankClient(
        settings(),
        transport=httpx.MockTransport(handler),
        production_approved=lambda: True,
    )
    initialized = await client.init_payment(
        order_id="renewal-order-uuid",
        amount_kopecks=49900,
        description="Car Wrap renewal",
        customer_key="1001",
        recurrent=True,
        operation_initiator_type="R",
    )

    assert initialized.payment_id == "p-2"
    assert initialized.payment_url is None
    payload = json.loads(calls[0].content)
    assert payload["Recurrent"] == "Y"
    assert payload["OperationInitiatorType"] == "R"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ConnectError("connect failed"), TBankRequestNotSent),
        (httpx.ConnectTimeout("connect timed out"), TBankRequestNotSent),
        (httpx.ReadTimeout("reply timed out"), TBankOutcomeAmbiguous),
        (httpx.WriteTimeout("upload timed out"), TBankOutcomeAmbiguous),
        (httpx.RemoteProtocolError("invalid reply"), TBankOutcomeAmbiguous),
    ],
)
async def test_init_classifies_pre_upload_and_ambiguous_transport_failures(
    error: httpx.HTTPError, expected: type[Exception]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    client = TBankClient(
        settings(),
        transport=httpx.MockTransport(handler),
        production_approved=lambda: True,
    )

    with pytest.raises(expected):
        await client.init_payment(
            order_id="payment-order-uuid",
            amount_kopecks=2500,
            description="Car Wrap generation",
            customer_key="1001",
            recurrent=False,
        )


@pytest.mark.asyncio
async def test_init_valid_unsuccessful_response_is_definitive_rejection() -> None:
    client = TBankClient(
        settings(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"Success": False, "ErrorCode": "7"})
        ),
        production_approved=lambda: True,
    )

    with pytest.raises(TBankInitRejected):
        await client.init_payment(
            order_id="payment-order-uuid",
            amount_kopecks=2500,
            description="Car Wrap generation",
            customer_key="1001",
            recurrent=False,
        )
