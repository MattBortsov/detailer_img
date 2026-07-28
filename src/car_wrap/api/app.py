"""Injectable FastAPI application factory."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from car_wrap.api.custom_colors import router as custom_colors_router
from car_wrap.api.routes.palette import router as palette_router
from car_wrap.api.routes.session import router as session_router
from car_wrap.config import AppSettings
from car_wrap.services.telegram_auth import exchange_init_data

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self' https://telegram.org; "
    "style-src 'self'; img-src 'self' blob:; connect-src 'self'; "
    "base-uri 'none'; object-src 'none'; form-action 'self'"
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def create_app(
    *,
    settings: AppSettings,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Callable[[], datetime] = utc_now,
    exchange_service: Callable[..., Any] = exchange_init_data,
    custom_color_service: Any = None,
    custom_color_storage: Any = None,
    custom_color_repository: Any = None,
    lifespan: Any = None,
) -> FastAPI:
    """Build an app with explicit configuration and persistence boundaries."""

    app = FastAPI(
        title="Car Wrap Mini App API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.clock = clock
    app.state.exchange_service = exchange_service
    app.state.custom_color_service = custom_color_service
    app.state.custom_color_storage = custom_color_storage
    app.state.custom_color_repository = custom_color_repository
    app.include_router(session_router)
    app.include_router(palette_router)
    app.include_router(custom_colors_router)

    @app.middleware("http")
    async def security_policy(request: Request, call_next: Any) -> Any:
        response: Response
        if request.url.scheme != "https":
            response = RedirectResponse(
                url=str(request.url.replace(scheme="https")),
                status_code=307,
            )
        else:
            try:
                response = await call_next(request)
            except Exception:
                response = JSONResponse(
                    status_code=500,
                    content={"detail": "Service unavailable"},
                )
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        if (
            request.url.path.startswith("/api/")
            and "cache-control" not in response.headers
        ):
            response.headers["Cache-Control"] = "no-store"
        return response

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

    frontend_directory = Path(__file__).resolve().parents[3] / "frontend"
    mini_app_path = urlsplit(settings.mini_app_url).path.rstrip("/") or "/"
    app.mount(
        mini_app_path,
        StaticFiles(directory=frontend_directory, html=True),
        name="mini-app",
    )
    return app
