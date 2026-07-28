"""PostgreSQL contracts for Phase 2 metadata persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from alembic import command
from car_wrap.db.base import Base
from car_wrap.db.models import ActiveSource, MiniAppSession
from car_wrap.db.session import create_session_factory
from tests.integration.conftest import validate_test_database_url

pytestmark = pytest.mark.postgresql


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


def test_phase2_core_tables_remain_metadata_only() -> None:
    assert {
        "active_sources",
        "mini_app_sessions",
    } <= set(Base.metadata.tables)
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
        for table_name in ("active_sources", "mini_app_sessions")
        for column in Base.metadata.tables[table_name].columns
    }
    assert not any(
        fragment in column for column in all_columns for fragment in forbidden_fragments
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
            MiniAppSession.__table__.insert().values(**mini_app_session_values())
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


def test_integration_harness_rejects_unsafe_database_targets() -> None:
    with pytest.raises(ValueError):
        validate_test_database_url("sqlite:///car_wrap_test.db")
    with pytest.raises(ValueError):
        validate_test_database_url("postgresql+psycopg://user:pass@db/car_wrap")


def alembic_config(test_database_url: str) -> Config:
    root = Path(__file__).parents[2]
    config = Config(root / "alembic.ini")
    config.attributes["database_url"] = test_database_url
    return config


def inspect_schema(sync_connection: object) -> dict[str, object]:
    schema = inspect(sync_connection)
    return {
        "tables": set(schema.get_table_names()),
        "active_columns": {
            column["name"] for column in schema.get_columns("active_sources")
        },
        "session_columns": {
            column["name"] for column in schema.get_columns("mini_app_sessions")
        },
        "active_checks": {
            item["name"] for item in schema.get_check_constraints("active_sources")
        },
        "session_checks": {
            item["name"] for item in schema.get_check_constraints("mini_app_sessions")
        },
        "session_uniques": {
            item["name"] for item in schema.get_unique_constraints("mini_app_sessions")
        },
    }


def test_alembic_upgrade_downgrade_upgrade_parity(
    test_database_url: str,
) -> None:
    config = alembic_config(test_database_url)
    engine = create_engine(test_database_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            Base.metadata.drop_all(connection)
            connection.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            observed = inspect_schema(connection)
        assert {
            "active_sources",
            "alembic_version",
            "mini_app_sessions",
            "custom_colors",
            "custom_color_versions",
            "moderation_attempts",
            "admin_audit_events",
        } <= observed["tables"]
        assert observed["active_columns"] == {
            column.name for column in ActiveSource.__table__.columns
        }
        assert observed["session_columns"] == {
            column.name for column in MiniAppSession.__table__.columns
        }

        command.downgrade(config, "base")
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) <= {"alembic_version"}
        command.upgrade(config, "head")
    finally:
        engine.dispose()


def test_migration_is_metadata_only() -> None:
    migration = (
        Path(__file__).parents[2]
        / "alembic/versions/0001_active_sources_and_mini_app_sessions.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "LargeBinary",
        "media_bytes",
        "base64",
        "image_url",
        "file_path",
        "generation_job",
        "outbox",
        "celery",
        "provider",
        "result",
        "delivery",
    )
    assert not any(fragment in migration for fragment in forbidden)
