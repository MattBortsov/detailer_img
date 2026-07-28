"""Transactional custom-color lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.custom_colors.moderation import ModerationResult, normalize_display_name
from car_wrap.db.models import (
    AdminAuditEvent,
    CustomColor,
    CustomColorVersion,
    ModerationAttempt,
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
        {
            ColorStatus.NEEDS_REVIEW,
            ColorStatus.REJECTED,
            ColorStatus.APPROVED,
            ColorStatus.DELETED,
        }
    ),
    ColorStatus.NEEDS_REVIEW: frozenset(
        {ColorStatus.REJECTED, ColorStatus.APPROVED, ColorStatus.DELETED}
    ),
    ColorStatus.REJECTED: frozenset(
        {ColorStatus.NEEDS_REVIEW, ColorStatus.APPROVED, ColorStatus.DELETED}
    ),
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
        owner_id: int | None = None,
        reason_code: str | None = None,
        admin_actor_id: int | None = None,
        admin_action: str | None = None,
        admin_reason: str | None = None,
    ) -> CustomColor:
        statement = select(CustomColor).where(CustomColor.id == color_id)
        if owner_id is not None:
            statement = statement.where(CustomColor.telegram_user_id == owner_id)
        color = await session.scalar(statement.with_for_update())
        if color is None:
            raise LookupError("custom color not found")
        current = ColorStatus(color.status)
        if admin_action == "restore" and current is not ColorStatus.HIDDEN:
            raise InvalidTransitionError("only hidden colors can be restored")
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

    async def rename(
        self,
        session: AsyncSession,
        *,
        color_id: UUID,
        display_name: str,
        owner_id: int | None = None,
        admin_actor_id: int | None = None,
        admin_reason: str | None = None,
    ) -> CustomColor:
        statement = select(CustomColor).where(
            CustomColor.id == color_id,
            CustomColor.status != ColorStatus.DELETED.value,
        )
        if owner_id is not None:
            statement = statement.where(CustomColor.telegram_user_id == owner_id)
        color = await session.scalar(statement.with_for_update())
        if color is None:
            raise LookupError("custom color not found")
        color.display_name = normalize_display_name(display_name)
        color.updated_at = datetime.now(UTC)
        if admin_actor_id is not None:
            session.add(
                AdminAuditEvent(
                    actor_telegram_user_id=admin_actor_id,
                    custom_color_id=color.id,
                    action="rename",
                    reason=admin_reason,
                )
            )
        await session.flush()
        return color

    async def apply_moderation(
        self,
        session: AsyncSession,
        *,
        color_id: UUID,
        idempotency_key: str,
        result: ModerationResult,
        provider_model: str,
    ) -> CustomColor:
        color = await session.scalar(
            select(CustomColor).where(CustomColor.id == color_id).with_for_update()
        )
        if color is None:
            raise LookupError("custom color not found")
        version = await session.scalar(
            select(CustomColorVersion).where(
                CustomColorVersion.custom_color_id == color.id,
                CustomColorVersion.version == color.current_version,
            )
        )
        if version is None:
            raise LookupError("custom color version not found")
        existing = await session.scalar(
            select(ModerationAttempt.id).where(
                ModerationAttempt.custom_color_version_id == version.id,
                ModerationAttempt.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return color
        disposition_value = str(result.disposition)
        if disposition_value not in {
            ColorStatus.APPROVED.value,
            ColorStatus.REJECTED.value,
            ColorStatus.NEEDS_REVIEW.value,
        }:
            raise ValueError("unsupported moderation disposition")
        reason_code = result.reason_code
        safety_confidence = result.safety_confidence
        domain_confidence = result.domain_confidence
        session.add(
            ModerationAttempt(
                custom_color_version_id=version.id,
                idempotency_key=idempotency_key,
                provider_model=provider_model,
                decision=disposition_value,
                reason_code=reason_code,
                safety_confidence=safety_confidence * 100,
                domain_confidence=domain_confidence * 100,
            )
        )
        now = datetime.now(UTC)
        if color.status in {
            ColorStatus.PENDING.value,
            ColorStatus.NEEDS_REVIEW.value,
        }:
            color.status = disposition_value
            color.reason_code = reason_code
            color.updated_at = now
            if disposition_value == ColorStatus.APPROVED.value:
                color.approved_at = now
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

    async def cleanup_key_for_version(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
    ) -> str | None:
        row = (
            await session.execute(
                select(
                    CustomColor.status,
                    CustomColorVersion.object_key,
                    CustomColorVersion.retain_count,
                )
                .join(
                    CustomColor,
                    CustomColor.id == CustomColorVersion.custom_color_id,
                )
                .where(CustomColorVersion.id == version_id)
            )
        ).one_or_none()
        if row is None:
            raise LookupError("custom color version not found")
        status, object_key, retain_count = row
        if status == ColorStatus.DELETED.value and retain_count == 0:
            return str(object_key)
        return None

    async def cleanup_key_for_color(
        self,
        session: AsyncSession,
        *,
        color_id: UUID,
    ) -> str | None:
        version_id = await session.scalar(
            select(CustomColorVersion.id)
            .join(
                CustomColor,
                CustomColor.id == CustomColorVersion.custom_color_id,
            )
            .where(
                CustomColor.id == color_id,
                CustomColor.status == ColorStatus.DELETED.value,
                CustomColor.current_version == CustomColorVersion.version,
            )
        )
        if version_id is None:
            raise LookupError("custom color version not found")
        return await self.cleanup_key_for_version(
            session,
            version_id=version_id,
        )
