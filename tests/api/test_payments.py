"""Public T-Bank acknowledgement boundary."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from car_wrap.api.app import create_app
from car_wrap.config import AppSettings


class EmptySessions:
    def __call__(self) -> Any:
        raise AssertionError("invalid webhooks must not open a database session")


@pytest.mark.asyncio
async def test_tbank_webhook_acknowledges_malformed_input_without_details() -> None:
    settings = AppSettings.model_validate(
        {
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
    )
    app = create_app(settings=settings, session_factory=EmptySessions())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/payments/tbank/webhook", content=b"not-json"
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert "password-canary" not in response.text
