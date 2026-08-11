"""Telegram initData-to-cookie exchange route."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse

from car_wrap.api.routes.palette import build_palette_state
from car_wrap.api.schemas import PaletteStateOut
from car_wrap.services.telegram_auth import (
    IssuedMiniAppSession,
    TelegramAuthenticationError,
)
from car_wrap.services.telegram_users import record_telegram_user

router = APIRouter(prefix="/api/v1/tma", tags=["telegram-session"])


def unauthorized_response(bot_chat_url: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Unauthorized"},
        headers={
            "Cache-Control": "no-store",
            "X-Bot-Chat-Url": bot_chat_url,
        },
    )


def raw_authorization_headers(request: Request) -> list[bytes]:
    return [
        value
        for name, value in request.scope.get("headers", [])
        if name.lower() == b"authorization"
    ]


async def exchange_session(
    request: Request,
) -> tuple[IssuedMiniAppSession | None, Response | None]:
    """Validate launch evidence and issue the opaque owner-bound session."""

    headers = raw_authorization_headers(request)
    settings = request.app.state.settings
    bot_chat_url = settings.bot_chat_url
    if len(headers) != 1 or request.query_params:
        return None, unauthorized_response(bot_chat_url)
    raw_header = headers[0]
    prefix = b"tma "
    if (
        not raw_header.startswith(prefix)
        or len(raw_header) <= len(prefix)
        or len(raw_header) - len(prefix) > settings.init_data_max_bytes
    ):
        return None, unauthorized_response(bot_chat_url)
    try:
        raw_init_data = raw_header[len(prefix) :].decode("utf-8")
    except UnicodeDecodeError:
        return None, unauthorized_response(bot_chat_url)

    try:
        async with request.app.state.session_factory() as session:
            issued = await request.app.state.exchange_service(
                session,
                raw_init_data,
                settings=settings,
                now=request.app.state.clock(),
            )
            await record_telegram_user(session, issued.telegram_user_id)
            await session.commit()
    except TelegramAuthenticationError:
        return None, unauthorized_response(bot_chat_url)

    return issued, None


def session_response(
    request: Request,
    issued: IssuedMiniAppSession,
    *,
    status_code: int,
) -> Response:
    """Attach the opaque session cookie without exposing Telegram launch data."""

    settings = request.app.state.settings
    response = Response(
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )
    response.set_cookie(
        key=settings.session_cookie_name,
        value=issued.token.get_secret_value(),
        max_age=settings.session_ttl_seconds,
        path="/api/v1",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/session", status_code=status.HTTP_204_NO_CONTENT)
async def create_session(request: Request) -> Response:
    """Accept initData only through one bounded `Authorization: tma` header."""

    issued, failure = await exchange_session(request)
    if failure is not None:
        return failure
    if issued is None:
        return unauthorized_response(request.app.state.settings.bot_chat_url)

    return session_response(request, issued, status_code=status.HTTP_204_NO_CONTENT)


@router.post("/session/bootstrap", response_model=PaletteStateOut)
async def create_session_and_load_palette(request: Request) -> Response:
    """Exchange launch data and return the first owner-bound screen in one RTT."""

    issued, failure = await exchange_session(request)
    if failure is not None:
        return failure
    if issued is None:
        return unauthorized_response(request.app.state.settings.bot_chat_url)

    settings = request.app.state.settings
    async with request.app.state.session_factory() as session:
        payload = await build_palette_state(
            session,
            settings=settings,
            telegram_user_id=issued.telegram_user_id,
            session_expires_at=issued.expires_at,
        )
    response = JSONResponse(content=payload.model_dump(mode="json"))
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        key=settings.session_cookie_name,
        value=issued.token.get_secret_value(),
        max_age=settings.session_ttl_seconds,
        path="/api/v1",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response
