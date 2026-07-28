"""Alembic environment with an explicit, redaction-safe database target."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from car_wrap.db import models as database_models  # noqa: F401
from car_wrap.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    """Resolve the URL without echoing it through Alembic configuration."""

    supplied = config.attributes.get("database_url")
    if not isinstance(supplied, str):
        supplied = context.get_x_argument(as_dictionary=True).get("database_url")
    if not isinstance(supplied, str):
        raise RuntimeError("an explicit database URL is required")
    if not supplied.startswith("postgresql+psycopg://"):
        raise RuntimeError("database URL must use PostgreSQL with Psycopg")
    return supplied


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_sync_migrations(connection: object) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": database_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(run_sync_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
