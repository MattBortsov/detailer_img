"""PostgreSQL contracts for Phase 2 metadata persistence."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from car_wrap.db.base import Base
from car_wrap.db.models import ActiveSource, MiniAppSession
from car_wrap.db.session import create_session_factory

pytestmark = pytest.mark.postgresql


@pytest_asyncio.fixture
async def database_engine() -> AsyncIterator[AsyncEngine]:
    database_url = os.environ["CAR_WRAP_TEST_DATABASE_URL"]
    assert database_url.startswith("postgresql+psycopg://")
    assert "test" in database_url.rsplit("/", maxsplit=1)[-1]
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def active_source_values(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "telegram_user_id": 1001,
        "chat_id": 1001,
        "source_message_id": 17,
        "telegram_file_id": "file-id",
        "telegram_file_unique_id": "file-unique-id",
        "media_kind": "photo",
        "mime_type": "image/jpeg",
        "byte_size": 1024,
        "width": 1200,
        "height": 800,
        "accepted_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return values


def mini_app_session_values(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "id": uuid4(),
        "token_sha256": "a" * 64,
        "init_data_sha256": "b" * 64,
        "telegram_user_id": 1001,
        "auth_date": now,
        "created_at": now,
        "expires_at": now + timedelta(minutes=15),
        "revoked_at": None,
    }
    values.update(overrides)
    return values


def test_metadata_contains_only_two_metadata_tables() -> None:
    assert set(Base.metadata.tables) == {
        "active_sources",
        "mini_app_sessions",
    }
    forbidden_fragments = {
        "bytes",
        "base64",
        "url",
        "path",
        "blob",
        "json",
        "prompt",
        "model",
        "provider",
        "result",
        "delivery",
        "job",
        "queue",
    }
    all_columns = {
        column.name
        for table in Base.metadata.tables.values()
        for column in table.columns
    }
    assert not any(
        fragment in column
        for column in all_columns
        for fragment in forbidden_fragments
    )


@pytest.mark.asyncio
async def test_schema_enforces_active_source_scalar_contracts(
    database_engine: AsyncEngine,
) -> None:
    invalid_values = (
        {"telegram_user_id": 0},
        {"source_message_id": 0},
        {"byte_size": 0},
        {"width": 0},
        {"height": 0},
        {"media_kind": "video"},
        {"mime_type": "image/gif"},
    )

    for overrides in invalid_values:
        with pytest.raises(IntegrityError):
            async with database_engine.begin() as connection:
                await connection.execute(
                    ActiveSource.__table__.insert().values(
                        **active_source_values(**overrides)
                    )
                )


@pytest.mark.asyncio
async def test_schema_enforces_session_digest_and_expiry_contracts(
    database_engine: AsyncEngine,
) -> None:
    async with database_engine.begin() as connection:
        await connection.execute(
            MiniAppSession.__table__.insert().values(
                **mini_app_session_values()
            )
        )

    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                MiniAppSession.__table__.insert().values(
                    **mini_app_session_values(token_sha256="a" * 64)
                )
            )

    now = datetime.now(UTC)
    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                MiniAppSession.__table__.insert().values(
                    **mini_app_session_values(
                        token_sha256="c" * 64,
                        init_data_sha256="d" * 64,
                        created_at=now,
                        expires_at=now,
                    )
                )
            )


@pytest.mark.asyncio
async def test_session_factory_is_lazy_psycopg_and_unpooled() -> None:
    engine, sessions = create_session_factory(
        "postgresql+psycopg://user:pass@localhost/test",
        poolclass=NullPool,
    )
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert isinstance(engine.pool, NullPool)
        assert sessions.class_.__name__ == "AsyncSession"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_schema_can_be_introspected(
    database_engine: AsyncEngine,
) -> None:
    async with database_engine.connect() as connection:
        tables = await connection.run_sync(
            lambda sync_connection: set(inspect(sync_connection).get_table_names())
        )
    assert {"active_sources", "mini_app_sessions"} <= tables
