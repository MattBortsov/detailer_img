"""Lazy process-local construction of async database sessions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import Pool

AsyncEngineFactory = Callable[..., AsyncEngine]


def create_session_factory(
    database_url: str | SecretStr,
    *,
    engine_factory: AsyncEngineFactory = create_async_engine,
    poolclass: type[Pool] | None = None,
    **engine_options: Any,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create an engine and sessionmaker without import-time side effects."""

    revealed_url = (
        database_url.get_secret_value()
        if isinstance(database_url, SecretStr)
        else database_url
    )
    if not revealed_url.startswith("postgresql+psycopg://"):
        raise ValueError("database URL must use PostgreSQL with Psycopg")
    options: dict[str, Any] = {
        "pool_pre_ping": True,
        **engine_options,
    }
    if poolclass is not None:
        options["poolclass"] = poolclass
    engine = engine_factory(revealed_url, **options)
    sessions = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return engine, sessions
