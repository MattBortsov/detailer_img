"""Injectable FastAPI application factory."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from car_wrap.api.routes.palette import router as palette_router
from car_wrap.api.routes.session import router as session_router
from car_wrap.config import AppSettings
from car_wrap.services.telegram_auth import exchange_init_data


def utc_now() -> datetime:
    return datetime.now(UTC)


def create_app(
    *,
    settings: AppSettings,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Callable[[], datetime] = utc_now,
    exchange_service: Callable[..., Any] = exchange_init_data,
) -> FastAPI:
    """Build an app with explicit configuration and persistence boundaries."""

    app = FastAPI(
        title="Car Wrap Mini App API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.clock = clock
    app.state.exchange_service = exchange_service
    app.include_router(session_router)
    app.include_router(palette_router)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        del request, error
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid request"},
            headers={"Cache-Control": "no-store"},
        )

    return app
