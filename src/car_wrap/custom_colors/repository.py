"""Transactional custom-color lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.db.models import (
    AdminAuditEvent,
    CustomColor,
    CustomColorVersion,
)


class ColorStatus(StrEnum):
    PENDING = "pending"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    APPROVED = "approved"
    HIDDEN = "hidden"
    DELETED = "deleted"


class QuotaExceededError(ValueError):
    """The owner already has the configured number of active colors."""


class InvalidTransitionError(ValueError):
    """The requested lifecycle transition is not allowed."""


@dataclass(frozen=True, slots=True)
class VersionInput:
    object_key: str
    sha256: str
    byte_size: int
    width: int
    height: int


_TRANSITIONS: dict[ColorStatus, frozenset[ColorStatus]] = {
    ColorStatus.PENDING: frozenset(
        {ColorStatus.NEEDS_REVIEW, ColorStatus.REJECTED, ColorStatus.APPROVED}
    ),
    ColorStatus.NEEDS_REVIEW: frozenset({ColorStatus.REJECTED, ColorStatus.APPROVED}),
    ColorStatus.REJECTED: frozenset({ColorStatus.NEEDS_REVIEW, ColorStatus.DELETED}),
    ColorStatus.APPROVED: frozenset({ColorStatus.HIDDEN, ColorStatus.DELETED}),
    ColorStatus.HIDDEN: frozenset({ColorStatus.APPROVED, ColorStatus.DELETED}),
    ColorStatus.DELETED: frozenset(),
}


class CustomColorRepository:
    def __init__(self, *, quota: int) -> None:
        if quota <= 0:
            raise ValueError("quota must be positive")
        self._quota = quota

    async def create(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        display_name: str,
        version: VersionInput,
    ) -> CustomColor:
        if owner_id <= 0:
            raise ValueError("owner ID must be positive")
        # A per-owner transaction-level advisory lock makes count+insert atomic
        # even when this owner has no rows yet to lock.
        await session.execute(select(func.pg_advisory_xact_lock(owner_id)))
        active_count = await session.scalar(
            select(func.count(CustomColor.id)).where(
                CustomColor.telegram_user_id == owner_id,
                CustomColor.status != ColorStatus.DELETED.value,
            )
        )
        if active_count is None or active_count >= self._quota:
            raise QuotaExceededError("custom color quota reached")
        color = CustomColor(
            telegram_user_id=owner_id,
            display_name=display_name,
            status=ColorStatus.PENDING.value,
            current_version=1,
        )
        session.add(color)
        await session.flush()
        session.add(
            CustomColorVersion(
                custom_color_id=color.id,
                version=1,
                object_key=version.object_key,
                sha256=version.sha256,
                byte_size=version.byte_size,
                width=version.width,
                height=version.height,
            )
        )
        await session.flush()
        return color

    async def transition(
        self,
        session: AsyncSession,
        *,
        color_id: UUID,
        target: ColorStatus,
        reason_code: str | None = None,
        admin_actor_id: int | None = None,
        admin_action: str | None = None,
        admin_reason: str | None = None,
    ) -> CustomColor:
        color = await session.scalar(
            select(CustomColor).where(CustomColor.id == color_id).with_for_update()
        )
        if color is None:
            raise LookupError("custom color not found")
        current = ColorStatus(color.status)
        if target not in _TRANSITIONS[current]:
            raise InvalidTransitionError(f"{current} cannot transition to {target}")
        now = datetime.now(UTC)
        color.status = target.value
        color.reason_code = reason_code
        color.updated_at = now
        if target is ColorStatus.APPROVED:
            color.approved_at = now
            color.deleted_at = None
        elif target is ColorStatus.DELETED:
            color.deleted_at = now
        if admin_actor_id is not None:
            if not admin_action:
                raise ValueError("admin action is required for audited mutation")
            session.add(
                AdminAuditEvent(
                    actor_telegram_user_id=admin_actor_id,
                    custom_color_id=color.id,
                    action=admin_action,
                    reason=admin_reason,
                )
            )
        await session.flush()
        return color

    async def resolve_approved_version(
        self,
        session: AsyncSession,
        *,
        color_id: UUID,
        version: int,
    ) -> CustomColorVersion | None:
        resolved = await session.scalar(
            select(CustomColorVersion)
            .join(
                CustomColor,
                CustomColor.id == CustomColorVersion.custom_color_id,
            )
            .where(
                CustomColor.id == color_id,
                CustomColor.status == ColorStatus.APPROVED.value,
                CustomColor.current_version == version,
                CustomColorVersion.version == version,
            )
        )
        return resolved

    async def retain(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
    ) -> None:
        updated_id = await session.scalar(
            update(CustomColorVersion)
            .where(CustomColorVersion.id == version_id)
            .values(retain_count=CustomColorVersion.retain_count + 1)
            .returning(CustomColorVersion.id)
        )
        if updated_id is None:
            raise LookupError("custom color version not found")

    async def release(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
    ) -> int:
        version = await session.scalar(
            select(CustomColorVersion)
            .where(CustomColorVersion.id == version_id)
            .with_for_update()
        )
        if version is None:
            raise LookupError("custom color version not found")
        if version.retain_count <= 0:
            raise ValueError("custom color version is not retained")
        version.retain_count -= 1
        await session.flush()
        return version.retain_count
