"""HMAC-authenticated payment confirmation from the SeoSmith gateway."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from car_wrap.billing.gateway import SIGNATURE_HEADER, TIMESTAMP_HEADER
from car_wrap.billing.payments import PaymentConfirmationError, PaymentService
from car_wrap.bot.delivery import PAYMENT_CONFIRMED_COPY, payment_confirmed_keyboard

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])
logger = logging.getLogger(__name__)

MAX_RESULT_BYTES = 8 * 1024


class GatewayResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source: Literal["car_wrap_bot"]
    status: Literal["paid"]
    external_order_id: str = Field(min_length=1, max_length=120)
    invoice_id: int = Field(gt=0, le=2_147_483_647)
    amount_kopecks: int = Field(gt=0, le=2_147_483_647)


@router.post("/gateway/result")
async def payment_gateway_result(request: Request) -> JSONResponse:
    """Grant only a fresh signed, amount-matched SeoSmith notification."""

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_RESULT_BYTES:
            return JSONResponse({"ok": False}, status_code=400)
        body.extend(chunk)

    service: PaymentService | None = request.app.state.payment_service
    if service is None:
        return JSONResponse({"ok": False}, status_code=503)
    raw_body = bytes(body)
    if not service.verify_gateway_callback(
        timestamp=request.headers.get(TIMESTAMP_HEADER),
        signature=request.headers.get(SIGNATURE_HEADER),
        body=raw_body,
    ):
        return JSONResponse({"ok": False}, status_code=401)
    try:
        payload = GatewayResult.model_validate_json(raw_body)
    except ValidationError:
        return JSONResponse({"ok": False}, status_code=400)

    try:
        credited_user_id = await service.confirm_result(
            external_order_id=payload.external_order_id,
            invoice_id=payload.invoice_id,
            amount_kopecks=payload.amount_kopecks,
        )
    except PaymentConfirmationError:
        logger.warning(
            "SeoSmith payment result rejected",
            extra={"invoice_id": payload.invoice_id},
        )
        return JSONResponse({"ok": False}, status_code=400)

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
