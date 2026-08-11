"""Public, deliberately uninformative T-Bank webhook acknowledgement."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from car_wrap.billing.payments import PaymentService
from car_wrap.billing.tbank import verify_notification_token
from car_wrap.bot.delivery import PAYMENT_CONFIRMED_COPY, payment_confirmed_keyboard

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


@router.post("/tbank/webhook")
async def tbank_webhook(request: Request) -> JSONResponse:
    """Always acknowledge untrusted callbacks; only valid confirmations mutate state."""

    try:
        payload: Any = await request.json()
    except Exception:
        return JSONResponse({"ok": True})
    if not isinstance(payload, dict) or not verify_notification_token(
        payload, request.app.state.settings.tbank_password
    ):
        return JSONResponse({"ok": True})
    service: PaymentService | None = request.app.state.payment_service
    if service is not None:
        credited_user_id = await service.confirm_webhook(payload)
        bot = request.app.state.telegram_bot
        if credited_user_id is not None and bot is not None:
            with suppress(Exception):
                await bot.send_message(
                    credited_user_id,
                    PAYMENT_CONFIRMED_COPY,
                    reply_markup=payment_confirmed_keyboard(
                        request.app.state.settings.mini_app_url
                    ),
                )
    return JSONResponse({"ok": True})
