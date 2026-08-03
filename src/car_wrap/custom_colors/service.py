"""Custom color creation workflow with deterministic compensation."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.custom_colors.analysis import (
    ANALYSIS_REVISION,
    ColorStructure,
    ReferenceAnalysisError,
    ReferenceProfile,
    SurfaceFinish,
    analyze_reference,
)
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

    def read(self, key: str, expected_sha256: str) -> bytes: ...

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

    async def apply_analysis(
        self,
        session: AsyncSession,
        *,
        color_id: UUID,
        analysis_revision: str,
        color_profile: dict[str, object],
    ) -> None: ...

    async def rename(
        self,
        session: AsyncSession,
        *,
        color_id: UUID,
        display_name: str,
        owner_id: int | None = None,
    ) -> Any: ...

    async def edit_details(
        self,
        session: AsyncSession,
        *,
        color_id: UUID,
        display_name: str,
        color_structure: str,
        finish: str,
        analysis_revision: str | None,
        color_profile: dict[str, object] | None,
        admin_actor_id: int,
        admin_reason: str | None = None,
    ) -> Any: ...

    async def current_version(
        self,
        session: AsyncSession,
        *,
        color_id: UUID,
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
Analyze = Callable[
    [bytes, ColorStructure, SurfaceFinish, ModerationResult],
    ReferenceProfile,
]


class CustomColorService:
    def __init__(
        self,
        *,
        storage: Storage,
        repository: Repository,
        normalize: Normalize,
        moderate: Moderate,
        analyze: Analyze = analyze_reference,
        moderation_model: str,
    ) -> None:
        self._storage = storage
        self._repository = repository
        self._normalize = normalize
        self._moderate = moderate
        self._analyze = analyze
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
        color_structure: ColorStructure | str = ColorStructure.UNSPECIFIED,
        finish: SurfaceFinish | str = SurfaceFinish.UNSPECIFIED,
    ) -> Any:
        if owner_id <= 0:
            raise ValueError("owner ID must be positive")
        if not _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
            raise ValueError("invalid idempotency key")
        try:
            normalized_structure = ColorStructure(color_structure)
            normalized_finish = SurfaceFinish(finish)
        except ValueError:
            raise ValueError("invalid custom color metadata") from None
        if (normalized_structure is ColorStructure.UNSPECIFIED) != (
            normalized_finish is SurfaceFinish.UNSPECIFIED
        ):
            raise ValueError("color structure and finish must be selected together")
        canonical = self._normalize(upload, declared_mime)
        name_was_missing = display_name == ""
        normalized_name = display_name or "Без названия"
        normalized_name = normalize_display_name(normalized_name)
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
                    color_structure=normalized_structure.value,
                    finish=normalized_finish.value,
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
        profile: ReferenceProfile | None = None
        if (
            normalized_structure is not ColorStructure.UNSPECIFIED
            and result.disposition is not ModerationDisposition.REJECTED
        ):
            try:
                profile = self._analyze(
                    canonical.data,
                    normalized_structure,
                    normalized_finish,
                    result,
                )
            except ReferenceAnalysisError:
                result = ModerationResult(
                    ModerationDisposition.NEEDS_REVIEW,
                    "reference_analysis_uncertain",
                    result.safety_confidence,
                    result.domain_confidence,
                    result.material_regions,
                    result.excluded_regions,
                    result.localization_confidence,
                    result.suggested_display_name,
                )
        try:
            if (
                name_was_missing
                and result.disposition is not ModerationDisposition.REJECTED
                and result.suggested_display_name is not None
            ):
                color = await self._repository.rename(
                    session,
                    color_id=color.id,
                    display_name=result.suggested_display_name,
                    owner_id=owner_id,
                )
            if profile is not None:
                await self._repository.apply_analysis(
                    session,
                    color_id=color.id,
                    analysis_revision=ANALYSIS_REVISION,
                    color_profile=profile.to_dict(),
                )
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

    async def edit_details(
        self,
        session: AsyncSession,
        *,
        color_id: UUID,
        display_name: str,
        color_structure: ColorStructure | str,
        finish: SurfaceFinish | str,
        admin_actor_id: int,
        admin_reason: str | None = None,
    ) -> Any:
        """Apply administrator metadata changes without changing the reference file."""

        try:
            normalized_structure = ColorStructure(color_structure)
            normalized_finish = SurfaceFinish(finish)
        except ValueError:
            raise ValueError("invalid custom color metadata") from None
        if (normalized_structure is ColorStructure.UNSPECIFIED) != (
            normalized_finish is SurfaceFinish.UNSPECIFIED
        ):
            raise ValueError("color structure and finish must be selected together")
        normalized_name = normalize_display_name(display_name)
        try:
            version = await self._repository.current_version(
                session,
                color_id=color_id,
            )
            profile: ReferenceProfile | None = None
            if normalized_structure is ColorStructure.UNSPECIFIED:
                analysis_revision = None
            elif (
                version.color_structure == normalized_structure.value
                and version.color_profile is not None
            ):
                existing_profile = ReferenceProfile.from_dict(version.color_profile)
                profile = replace(existing_profile, finish=normalized_finish)
                analysis_revision = version.analysis_revision or ANALYSIS_REVISION
            else:
                source = self._storage.read(version.object_key, version.sha256)
                profile = self._analyze(
                    source,
                    normalized_structure,
                    normalized_finish,
                    ModerationResult(
                        ModerationDisposition.APPROVED,
                        "admin_metadata_edit",
                        100,
                        100,
                    ),
                )
                analysis_revision = ANALYSIS_REVISION
            color = await self._repository.edit_details(
                session,
                color_id=color_id,
                display_name=normalized_name,
                color_structure=normalized_structure.value,
                finish=normalized_finish.value,
                analysis_revision=analysis_revision,
                color_profile=(profile.to_dict() if profile is not None else None),
                admin_actor_id=admin_actor_id,
                admin_reason=admin_reason,
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
