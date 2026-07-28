"""Safe PostgreSQL-only fixtures for integration tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from car_wrap.db.base import Base


def validate_test_database_url(value: str) -> str:
    """Reject non-PostgreSQL and non-test database targets."""

    parsed = urlsplit(value)
    database_name = parsed.path.lstrip("/")
    if parsed.scheme != "postgresql+psycopg":
        raise ValueError("integration database must use PostgreSQL with Psycopg")
    if parsed.hostname is None or "test" not in database_name.lower():
        raise ValueError("integration database name must contain 'test'")
    return value


@pytest.fixture(scope="session")
def test_database_url() -> str:
    value = os.environ.get("CAR_WRAP_TEST_DATABASE_URL")
    if value is None:
        pytest.skip("CAR_WRAP_TEST_DATABASE_URL is required")
    return validate_test_database_url(value)


@pytest_asyncio.fixture
async def database_engine(
    test_database_url: str,
) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()
