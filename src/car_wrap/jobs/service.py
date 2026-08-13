"""Atomic server-owned generation-job acceptance."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.billing.allowances import AllowanceService, AllowanceUnavailable
from car_wrap.custom_colors.repository import CustomColorRepository
from car_wrap.db.models import CustomColor
from car_wrap.jobs.contracts import (
    AcceptanceErrorCode,
    AcceptedJob,
    IntentKind,
    IntentSnapshot,
    JobAcceptanceError,
    JobStatus,
    SourceSnapshot,
)
from car_wrap.jobs.repository import JobRepository
from car_wrap.palette import (
    PaletteChoice,
    PaletteLookupError,
    SurpriseChoice,
    get_palette_choice,
    parse_custom_selection,
)


class JobAcceptanceService:
    def __init__(
        self,
        *,
        repository: JobRepository,
        custom_colors: CustomColorRepository,
        image_model: str,
        prompt_revision: str,
        max_active: int,
        max_recent: int,
        window_seconds: int,
        allowance_exempt_user_ids: tuple[int, ...] = (),
        clock: Callable[[], datetime] | None = None,
        allowances: AllowanceService | None = None,
    ) -> None:
        self._repository = repository
        self._custom_colors = custom_colors
        self._image_model = image_model
        self._prompt_revision = prompt_revision
        self._max_active = max_active
        self._max_recent = max_recent
        self._window_seconds = window_seconds
        self._allowance_exempt_user_ids = frozenset(allowance_exempt_user_ids)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._allowances = allowances or AllowanceService()

    async def accept(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        color_id: str,
        submission_uuid: UUID,
    ) -> AcceptedJob:
        try:
            await self._repository.lock_user(session, user_id)
            existing = await self._repository.existing(
                session,
                user_id=user_id,
                submission_uuid=submission_uuid,
            )
            if existing is not None:
                await session.commit()
                return AcceptedJob(existing.id, JobStatus(existing.status))

            active = await self._repository.source(session, user_id=user_id)
            if active is None:
                raise JobAcceptanceError(AcceptanceErrorCode.NO_SOURCE)
            now = self._clock()
            await self._repository.enforce_limits(
                session,
                user_id=user_id,
                now=now,
                max_active=self._max_active,
                max_recent=self._max_recent,
                window_seconds=self._window_seconds,
            )
            intent = await self._resolve_intent(session, color_id)
            job = await self._repository.create(
                session,
                user_id=user_id,
                submission_uuid=submission_uuid,
                source=SourceSnapshot.from_active_source(active),
                intent=intent,
                image_model=self._image_model,
                prompt_revision=self._prompt_revision,
                now=now,
            )
            if user_id not in self._allowance_exempt_user_ids:
                try:
                    await self._allowances.reserve(
                        session, user_id=user_id, job_id=job.id, now=now
                    )
                except AllowanceUnavailable as error:
                    raise JobAcceptanceError(
                        AcceptanceErrorCode.ALLOWANCE_REQUIRED
                    ) from error
            await session.commit()
            return AcceptedJob(job.id, JobStatus.QUEUED)
        except Exception:
            await session.rollback()
            raise

    async def _resolve_intent(
        self,
        session: AsyncSession,
        color_id: str,
    ) -> IntentSnapshot:
        try:
            choice = get_palette_choice(color_id)
        except PaletteLookupError:
            return await self._resolve_custom_intent(session, color_id)
        if isinstance(choice, PaletteChoice):
            return IntentSnapshot(
                kind=IntentKind.PALETTE,
                palette_color_id=choice.color_id,
                display_name=choice.ui_name_ru,
            )
        if isinstance(choice, SurpriseChoice):
            return IntentSnapshot(
                kind=IntentKind.SURPRISE,
                display_name=choice.ui_name_ru,
            )
        raise JobAcceptanceError(AcceptanceErrorCode.INVALID_SELECTION)

    async def _resolve_custom_intent(
        self,
        session: AsyncSession,
        color_id: str,
    ) -> IntentSnapshot:
        try:
            requested = parse_custom_selection(color_id)
        except PaletteLookupError:
            raise JobAcceptanceError(AcceptanceErrorCode.INVALID_SELECTION) from None
        version = await self._custom_colors.resolve_approved_version(
            session,
            color_id=requested.color_id,
            version=requested.version,
        )
        if version is None:
            raise JobAcceptanceError(AcceptanceErrorCode.INVALID_SELECTION)
        display_name = await session.scalar(
            select(CustomColor.display_name).where(
                CustomColor.id == version.custom_color_id
            )
        )
        if not isinstance(display_name, str):
            raise JobAcceptanceError(AcceptanceErrorCode.INVALID_SELECTION)
        try:
            await self._custom_colors.retain(session, version_id=version.id)
        except LookupError:
            raise JobAcceptanceError(AcceptanceErrorCode.INVALID_SELECTION) from None
        return IntentSnapshot(
            kind=IntentKind.CUSTOM,
            display_name=display_name,
            custom_color_version_id=version.id,
            custom_color_sha256=version.sha256,
        )
