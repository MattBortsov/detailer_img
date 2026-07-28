"""Custom color creation workflow with deterministic compensation."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.custom_colors.media import CanonicalImage
from car_wrap.custom_colors.moderation import (
    ModerationDisposition,
    ModerationResult,
    normalize_display_name,
)
from car_wrap.custom_colors.repository import ColorStatus, VersionInput
from car_wrap.custom_colors.storage import StoredObject

_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


class Storage(Protocol):
    def put(self, data: bytes) -> StoredObject: ...

    def delete(self, key: str) -> None: ...


class Repository(Protocol):
    async def create(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        display_name: str,
        version: VersionInput,
    ) -> Any: ...

    async def apply_moderation(
        self,
        session: AsyncSession,
        *,
        color_id: UUID,
        idempotency_key: str,
        result: ModerationResult,
        provider_model: str,
    ) -> Any: ...

    async def release(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
    ) -> int: ...

    async def cleanup_key_for_version(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
    ) -> str | None: ...

    async def cleanup_key_for_color(
        self,
        session: AsyncSession,
        *,
        color_id: UUID,
    ) -> str | None: ...

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
    ) -> Any: ...


Normalize = Callable[[bytes, str], CanonicalImage]
Moderate = Callable[[bytes], Awaitable[ModerationResult]]


class CustomColorService:
    def __init__(
        self,
        *,
        storage: Storage,
        repository: Repository,
        normalize: Normalize,
        moderate: Moderate,
        moderation_model: str,
    ) -> None:
        self._storage = storage
        self._repository = repository
        self._normalize = normalize
        self._moderate = moderate
        self._moderation_model = moderation_model

    async def create(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        display_name: str,
        upload: bytes,
        declared_mime: str,
        idempotency_key: str,
    ) -> Any:
        if owner_id <= 0:
            raise ValueError("owner ID must be positive")
        if not _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
            raise ValueError("invalid idempotency key")
        normalized_name = normalize_display_name(display_name)
        canonical = self._normalize(upload, declared_mime)
        stored = self._storage.put(canonical.data)
        if stored.sha256 != canonical.sha256 or stored.byte_size != len(canonical.data):
            self._storage.delete(stored.key)
            raise ValueError("private storage integrity metadata mismatch")
        try:
            color = await self._repository.create(
                session,
                owner_id=owner_id,
                display_name=normalized_name,
                version=VersionInput(
                    object_key=stored.key,
                    sha256=stored.sha256,
                    byte_size=stored.byte_size,
                    width=canonical.width,
                    height=canonical.height,
                ),
            )
            await session.commit()
        except Exception:
            await session.rollback()
            self._storage.delete(stored.key)
            raise
        try:
            result = await self._moderate(canonical.data)
        except Exception:
            result = ModerationResult(
                ModerationDisposition.NEEDS_REVIEW,
                "provider_unavailable",
                0,
                0,
            )
        try:
            color = await self._repository.apply_moderation(
                session,
                color_id=color.id,
                idempotency_key=idempotency_key,
                result=result,
                provider_model=self._moderation_model,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        return color

    async def release(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
    ) -> int:
        """Release an accepted reference and remove a deleted unretained object."""

        try:
            remaining = await self._repository.release(
                session,
                version_id=version_id,
            )
            cleanup_key = await self._repository.cleanup_key_for_version(
                session,
                version_id=version_id,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        if remaining == 0 and cleanup_key is not None:
            self._storage.delete(cleanup_key)
        return remaining

    async def delete(
        self,
        session: AsyncSession,
        *,
        color_id: UUID,
        owner_id: int | None = None,
        admin_actor_id: int | None = None,
        admin_reason: str | None = None,
    ) -> Any:
        """Tombstone a color, then remove its unretained private object."""

        try:
            color = await self._repository.transition(
                session,
                color_id=color_id,
                target=ColorStatus.DELETED,
                owner_id=owner_id,
                reason_code=(
                    "owner_deleted" if owner_id is not None else "admin_delete"
                ),
                admin_actor_id=admin_actor_id,
                admin_action=("delete" if admin_actor_id is not None else None),
                admin_reason=admin_reason,
            )
            cleanup_key = await self._repository.cleanup_key_for_color(
                session,
                color_id=color_id,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        if cleanup_key is not None:
            self._storage.delete(cleanup_key)
        return color
