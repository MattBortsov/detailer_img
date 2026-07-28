"""Database and opaque-session dependencies for protected Mini App APIs."""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.db.models import MiniAppSession

_OPAQUE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


@dataclass(frozen=True, slots=True)
class CurrentMiniAppSession:
    """Server-owned owner identity resolved from an opaque cookie."""

    telegram_user_id: int
    expires_at: datetime


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
    )


async def database_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session


async def require_mini_app_session(
    request: Request,
    session: Annotated[AsyncSession, Depends(database_session)],
) -> CurrentMiniAppSession:
    """Resolve only current, non-revoked server session state."""

    settings = request.app.state.settings
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token is None or not _OPAQUE_TOKEN.fullmatch(raw_token):
        raise unauthorized()
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = request.app.state.clock()
    row = await session.scalar(
        select(MiniAppSession).where(
            MiniAppSession.token_sha256 == digest,
            MiniAppSession.revoked_at.is_(None),
            MiniAppSession.expires_at > now,
        )
    )
    if row is None:
        raise unauthorized()
    return CurrentMiniAppSession(
        telegram_user_id=row.telegram_user_id,
        expires_at=row.expires_at,
    )
