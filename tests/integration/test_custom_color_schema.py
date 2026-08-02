"""PostgreSQL invariants for versioned custom colors."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from car_wrap.custom_colors.moderation import (
    ModerationDisposition,
    ModerationResult,
)
from car_wrap.custom_colors.repository import (
    ColorStatus,
    CustomColorRepository,
    InvalidTransitionError,
    QuotaExceededError,
    VersionInput,
)
from car_wrap.db.models import (
    AdminAuditEvent,
    CustomColor,
    CustomColorVersion,
    ModerationAttempt,
)

pytestmark = pytest.mark.postgresql


def version_input(
    key: str,
    *,
    color_structure: str = "unspecified",
    finish: str = "unspecified",
) -> VersionInput:
    return VersionInput(
        object_key=key,
        sha256="a" * 64,
        byte_size=128,
        width=64,
        height=64,
        color_structure=color_structure,
        finish=finish,
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
async def test_reference_profile_metadata_is_constrained_and_immutable(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    repository = CustomColorRepository(quota=20)
    async with sessions() as session:
        color = await repository.create(
            session,
            owner_id=202,
            display_name="Red Matte",
            version=version_input(
                "aa/bb/" + "7" * 32 + ".png",
                color_structure="solid",
                finish="matte",
            ),
        )
        await repository.apply_analysis(
            session,
            color_id=color.id,
            analysis_revision="reference-v1",
            color_profile={"structure": "solid", "base_rgb_hex": "#C63228"},
        )
        with pytest.raises(ValueError, match="immutable"):
            await repository.apply_analysis(
                session,
                color_id=color.id,
                analysis_revision="reference-v2",
                color_profile={"structure": "solid", "base_rgb_hex": "#000000"},
            )
        await session.commit()

    async with sessions() as session:
        version = await session.scalar(
            select(CustomColorVersion).where(
                CustomColorVersion.custom_color_id == color.id
            )
        )
    assert version is not None
    assert version.color_structure == "solid"
    assert version.finish == "matte"
    assert version.analysis_revision == "reference-v1"
    assert version.color_profile == {
        "structure": "solid",
        "base_rgb_hex": "#C63228",
    }
    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                CustomColorVersion.__table__.update()
                .where(CustomColorVersion.id == version.id)
                .values(color_structure="gradient", finish="gloss")
            )


@pytest.mark.asyncio
async def test_profiled_reference_cannot_be_approved_without_analysis(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    repository = CustomColorRepository(quota=20)
    async with sessions() as session:
        color = await repository.create(
            session,
            owner_id=203,
            display_name="Uncertain Red",
            version=version_input(
                "aa/bb/" + "8" * 32 + ".png",
                color_structure="solid",
                finish="satin",
            ),
        )
        with pytest.raises(InvalidTransitionError, match="successful analysis"):
            await repository.transition(
                session,
                color_id=color.id,
                target=ColorStatus.APPROVED,
                admin_actor_id=1,
                admin_action="approve",
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


@pytest.mark.asyncio
async def test_moderation_application_is_idempotent(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    repository = CustomColorRepository(quota=20)
    async with sessions() as session:
        color = await repository.create(
            session,
            owner_id=909,
            display_name="Bronze",
            version=version_input("aa/bb/" + "d" * 32 + ".png"),
        )
        result = ModerationResult(
            ModerationDisposition.APPROVED,
            "approved",
            98,
            96,
        )
        await repository.apply_moderation(
            session,
            color_id=color.id,
            idempotency_key="moderation-1",
            result=result,
            provider_model="vision-model",
        )
        await repository.apply_moderation(
            session,
            color_id=color.id,
            idempotency_key="moderation-1",
            result=result,
            provider_model="vision-model",
        )
        await session.commit()

    async with sessions() as session:
        stored_color = await session.get(CustomColor, color.id)
        attempt_count = await session.scalar(select(func.count(ModerationAttempt.id)))
    assert stored_color is not None
    assert stored_color.status == "approved"
    assert attempt_count == 1


@pytest.mark.asyncio
async def test_owner_guards_and_admin_mutations_are_centralized(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    repository = CustomColorRepository(quota=20)
    async with sessions() as session:
        color = await repository.create(
            session,
            owner_id=707,
            display_name="Bronze",
            version=version_input("aa/bb/" + "e" * 32 + ".png"),
        )
        await session.commit()

    async with sessions() as session:
        with pytest.raises(LookupError):
            await repository.rename(
                session,
                color_id=color.id,
                owner_id=999,
                display_name="Stolen",
            )
        await session.rollback()

    async with sessions() as session:
        renamed = await repository.rename(
            session,
            color_id=color.id,
            display_name="  Bronze   Satin  ",
            admin_actor_id=1,
            admin_reason="manual review",
        )
        approved = await repository.transition(
            session,
            color_id=color.id,
            target=ColorStatus.APPROVED,
            reason_code="admin_approve",
            admin_actor_id=1,
            admin_action="approve",
        )
        await session.commit()
        events = list(
            (
                await session.execute(
                    select(AdminAuditEvent).order_by(AdminAuditEvent.created_at)
                )
            ).scalars()
        )

    assert renamed.display_name == "Bronze Satin"
    assert approved.status == "approved"
    assert [event.action for event in events] == ["rename", "approve"]


@pytest.mark.asyncio
async def test_restore_only_accepts_hidden_colors(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    repository = CustomColorRepository(quota=20)
    async with sessions() as session:
        color = await repository.create(
            session,
            owner_id=808,
            display_name="Copper",
            version=version_input("aa/bb/" + "f" * 32 + ".png"),
        )
        await repository.transition(
            session,
            color_id=color.id,
            target=ColorStatus.REJECTED,
            reason_code="unsafe",
        )
        with pytest.raises(InvalidTransitionError):
            await repository.transition(
                session,
                color_id=color.id,
                target=ColorStatus.APPROVED,
                admin_actor_id=1,
                admin_action="restore",
            )


@pytest.mark.asyncio
async def test_retain_serializes_with_deletion(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    repository = CustomColorRepository(quota=20)
    async with sessions() as setup:
        color = await repository.create(
            setup,
            owner_id=818,
            display_name="Satin",
            version=version_input("aa/bb/" + "9" * 32 + ".png"),
        )
        approved = await repository.transition(
            setup,
            color_id=color.id,
            target=ColorStatus.APPROVED,
            reason_code="approved",
        )
        version = await setup.scalar(
            select(CustomColorVersion).where(
                CustomColorVersion.custom_color_id == color.id
            )
        )
        assert approved.status == ColorStatus.APPROVED.value
        assert version is not None
        version_id = version.id
        await setup.commit()

    async with sessions() as deleting, sessions() as retaining:
        await repository.transition(
            deleting,
            color_id=color.id,
            target=ColorStatus.DELETED,
            owner_id=818,
            reason_code="owner_deleted",
        )
        retain_task = asyncio.create_task(
            repository.retain(retaining, version_id=version_id)
        )
        await asyncio.sleep(0.05)
        assert not retain_task.done()
        await deleting.commit()
        with pytest.raises(LookupError):
            await retain_task
        await retaining.rollback()
