"""Safe PostgreSQL-only fixtures shared by integration and privacy tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from car_wrap.db.base import Base
from tests.integration.conftest import validate_test_database_url


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
