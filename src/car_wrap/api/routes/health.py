"""Sanitized process and database health probes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(tags=["health"])

_NO_STORE = {"Cache-Control": "no-store"}


@router.get("/health/live", include_in_schema=False)
async def liveness() -> JSONResponse:
    """Report only that the API event loop can serve a request."""

    return JSONResponse({"status": "ok"}, headers=_NO_STORE)


@router.get("/health/ready", include_in_schema=False)
async def readiness(request: Request) -> JSONResponse:
    """Prove a database round trip without disclosing failure details."""

    sessions: Any = request.app.state.session_factory
    try:
        async with sessions() as session:
            result = await session.scalar(text("SELECT 1"))
        if result != 1:
            raise RuntimeError("unexpected readiness result")
    except Exception:
        return JSONResponse(
            {"status": "unavailable"},
            status_code=503,
            headers=_NO_STORE,
        )
    return JSONResponse({"status": "ready"}, headers=_NO_STORE)
