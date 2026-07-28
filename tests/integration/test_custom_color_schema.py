"""PostgreSQL invariants for versioned custom colors."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from car_wrap.custom_colors.repository import (
    CustomColorRepository,
    QuotaExceededError,
    VersionInput,
)
from car_wrap.db.models import CustomColor, CustomColorVersion

pytestmark = pytest.mark.postgresql


def version_input(key: str) -> VersionInput:
    return VersionInput(
        object_key=key,
        sha256="a" * 64,
        byte_size=128,
        width=64,
        height=64,
    )


@pytest.mark.asyncio
async def test_version_constraints(database_engine: AsyncEngine) -> None:
    color_id = uuid4()
    async with database_engine.begin() as connection:
        await connection.execute(
            CustomColor.__table__.insert().values(
                id=color_id,
                telegram_user_id=101,
                display_name="Bronze",
                status="pending",
                current_version=1,
            )
        )
    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                CustomColorVersion.__table__.insert().values(
                    id=uuid4(),
                    custom_color_id=color_id,
                    version=0,
                    object_key="aa/bb/" + "c" * 32 + ".png",
                    sha256="not-a-digest",
                    byte_size=0,
                    width=0,
                    height=0,
                )
            )


@pytest.mark.asyncio
async def test_quota_is_atomic_per_owner(database_engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    repository = CustomColorRepository(quota=1)

    async def create_one(suffix: str) -> str:
        async with sessions() as session:
            try:
                await repository.create(
                    session,
                    owner_id=777,
                    display_name=f"Color {suffix}",
                    version=version_input(
                        f"aa/bb/{suffix * 32}.png",
                    ),
                )
                await session.commit()
                return "created"
            except QuotaExceededError:
                await session.rollback()
                return "quota"

    outcomes = await asyncio.gather(create_one("a"), create_one("b"))
    assert sorted(outcomes) == ["created", "quota"]

    async with AsyncSession(database_engine) as session:
        count = await session.scalar(
            select(func.count(CustomColor.id)).where(
                CustomColor.telegram_user_id == 777
            )
        )
    assert count == 1
