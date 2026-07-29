"""Preservation-first orchestration for one claimed generation attempt."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar, cast
from uuid import UUID

from aiogram import Bot
from aiogram.utils.chat_action import ChatActionSender
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.bot.delivery import (
    DeliveryFailure,
    DeliveryFailureKind,
    TelegramSender,
    send_generation_started,
    send_recovery,
    send_result,
)
from car_wrap.bot.media import (
    MediaRejection,
    MediaRejectionCode,
    TelegramDownloader,
    read_snapshotted_media,
)
from car_wrap.config import AppSettings
from car_wrap.custom_colors.storage import PrivateStorage
from car_wrap.eval.image_validation import ImageValidationError, validate_image_bytes
from car_wrap.generation.contracts import (
    BuiltInColorIntent,
    CustomColorIntent,
    SurpriseIntent,
    custom_intent,
)
from car_wrap.generation.openrouter import build_generation_payload
from car_wrap.generation.provider import (
    OpenRouterImagesProvider,
    ProviderFailure,
    ProviderFailureKind,
)
from car_wrap.generation.result import (
    ResultNormalizationError,
    normalize_telegram_photo,
)
from car_wrap.jobs.contracts import (
    ClaimedAttempt,
    ExecutionErrorCode,
    IntentKind,
)
from car_wrap.jobs.worker_repository import LostLeaseError, WorkerRepository
from car_wrap.palette import (
    PaletteChoice,
    PaletteLookupError,
    SurpriseChoice,
    get_palette_choice,
)

T = TypeVar("T")
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
GenerationIntent = BuiltInColorIntent | CustomColorIntent | SurpriseIntent

_ERROR_SUMMARY: dict[ExecutionErrorCode, str] = {
    ExecutionErrorCode.SOURCE_UNAVAILABLE: "source could not be downloaded",
    ExecutionErrorCode.SOURCE_CHANGED: "source no longer matches accepted snapshot",
    ExecutionErrorCode.CUSTOM_REFERENCE_UNAVAILABLE: (
        "custom reference could not be verified"
    ),
    ExecutionErrorCode.PROVIDER_UNAVAILABLE: "generation service unavailable",
    ExecutionErrorCode.PROVIDER_REJECTED: "generation request rejected",
    ExecutionErrorCode.PROVIDER_INVALID_RESPONSE: "generation result was invalid",
    ExecutionErrorCode.PROVIDER_AMBIGUOUS: "generation outcome could not be confirmed",
    ExecutionErrorCode.RESULT_INVALID: "result could not be normalized safely",
    ExecutionErrorCode.DELIVERY_UNAVAILABLE: "result delivery unavailable",
    ExecutionErrorCode.DELIVERY_AMBIGUOUS: "result delivery could not be confirmed",
    ExecutionErrorCode.INTERNAL_FAILURE: "worker could not complete the request",
}


@dataclass(frozen=True, slots=True)
class WorkerOutcome:
    """Metadata-only terminal outcome safe for task results and logs."""

    job_id: UUID
    error_code: ExecutionErrorCode | None


class CustomReferenceError(ValueError):
    """The retained immutable custom reference cannot be trusted."""


class GenerationWorkerService:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        repository: WorkerRepository,
        downloader: TelegramDownloader,
        storage: PrivateStorage,
        provider: OpenRouterImagesProvider,
        sender: TelegramSender,
        settings: AppSettings,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._repository = repository
        self._downloader = downloader
        self._storage = storage
        self._provider = provider
        self._sender = sender
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute(self, attempt: ClaimedAttempt) -> WorkerOutcome:
        """Execute one already-claimed attempt with durable side-effect markers."""

        await send_generation_started(
            self._sender,
            chat_id=attempt.chat_id,
            source_message_id=attempt.source_message_id,
        )
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat_loop(attempt, stop_heartbeat),
            name=f"generation-heartbeat:{attempt.attempt_id}",
        )
        source = None
        custom_reference: bytes | None = None
        provider_image = None
        normalized = None
        payload = None
        heartbeat_stopped = False
        try:
            intent, custom_reference = await self._resolve_intent(attempt)
            source = await read_snapshotted_media(
                self._downloader,
                file_id=attempt.telegram_file_id,
                declared_mime_type=attempt.source_mime_type,
                expected_byte_size=attempt.source_byte_size,
                expected_width=attempt.source_width,
                expected_height=attempt.source_height,
                settings=self._settings,
            )
            await self._transaction(
                lambda session: self._repository.mark_source_ready(
                    session,
                    attempt,
                    now=self._clock(),
                )
            )
            payload = build_generation_payload(
                model=attempt.image_model,
                intent=intent,
                vehicle_bytes=source.data,
                vehicle_media_type=source.mime_type,
                color_reference_bytes=custom_reference,
            )
            await self._transaction(
                lambda session: self._repository.mark_provider_started(
                    session,
                    attempt,
                    now=self._clock(),
                )
            )

            async def record_safe_retry() -> None:
                await self._transaction(
                    lambda session: self._repository.record_safe_preupload_retry(
                        session,
                        attempt,
                        now=self._clock(),
                    )
                )

            async with ChatActionSender.upload_photo(
                chat_id=attempt.chat_id,
                bot=cast(Bot, self._sender),
                interval=4.0,
                initial_sleep=4.0,
            ):
                provider_image = await self._provider.generate(
                    payload,
                    on_safe_retry=record_safe_retry,
                )
            provider_receipt = provider_image.receipt
            await self._transaction(
                lambda session: self._repository.mark_provider_succeeded(
                    session,
                    attempt,
                    provider_receipt,
                    now=self._clock(),
                )
            )
            normalized = normalize_telegram_photo(
                provider_image.data,
                max_input_side=self._settings.provider_max_image_side_px,
                max_input_pixels=self._settings.provider_max_image_pixels,
                max_output_bytes=self._settings.telegram_result_max_bytes,
                max_side_sum=self._settings.telegram_result_max_side_sum,
            )
            await self._transaction(
                lambda session: self._repository.mark_delivering(
                    session,
                    attempt,
                    now=self._clock(),
                )
            )
            receipt = await send_result(
                self._sender,
                normalized,
                chat_id=attempt.chat_id,
                source_message_id=attempt.source_message_id,
                bot_username=self._settings.bot_username,
                mini_app_url=self._settings.mini_app_url,
            )
            await self._stop_heartbeat(stop_heartbeat, heartbeat)
            heartbeat_stopped = True
            await self._transaction(
                lambda session: self._repository.mark_succeeded(
                    session,
                    attempt,
                    receipt,
                    now=self._clock(),
                )
            )
            return WorkerOutcome(job_id=attempt.job_id, error_code=None)
        except MediaRejection as error:
            code = (
                ExecutionErrorCode.SOURCE_CHANGED
                if error.code is MediaRejectionCode.SOURCE_CHANGED
                else ExecutionErrorCode.SOURCE_UNAVAILABLE
            )
            return await self._fail(
                attempt,
                code,
                ambiguous=False,
                stop=stop_heartbeat,
                heartbeat=heartbeat,
            )
        except CustomReferenceError:
            return await self._fail(
                attempt,
                ExecutionErrorCode.CUSTOM_REFERENCE_UNAVAILABLE,
                ambiguous=False,
                stop=stop_heartbeat,
                heartbeat=heartbeat,
            )
        except ProviderFailure as error:
            code = {
                ProviderFailureKind.UNAVAILABLE: (
                    ExecutionErrorCode.PROVIDER_UNAVAILABLE
                ),
                ProviderFailureKind.REJECTED: ExecutionErrorCode.PROVIDER_REJECTED,
                ProviderFailureKind.INVALID_RESPONSE: (
                    ExecutionErrorCode.PROVIDER_INVALID_RESPONSE
                ),
                ProviderFailureKind.AMBIGUOUS: ExecutionErrorCode.PROVIDER_AMBIGUOUS,
            }[error.kind]
            return await self._fail(
                attempt,
                code,
                ambiguous=error.kind is ProviderFailureKind.AMBIGUOUS,
                stop=stop_heartbeat,
                heartbeat=heartbeat,
            )
        except ResultNormalizationError:
            return await self._fail(
                attempt,
                ExecutionErrorCode.RESULT_INVALID,
                ambiguous=False,
                stop=stop_heartbeat,
                heartbeat=heartbeat,
            )
        except DeliveryFailure as error:
            code = (
                ExecutionErrorCode.DELIVERY_AMBIGUOUS
                if error.kind is DeliveryFailureKind.AMBIGUOUS
                else ExecutionErrorCode.DELIVERY_UNAVAILABLE
            )
            return await self._fail(
                attempt,
                code,
                ambiguous=error.kind is DeliveryFailureKind.AMBIGUOUS,
                stop=stop_heartbeat,
                heartbeat=heartbeat,
            )
        except LostLeaseError:
            raise
        except Exception:
            return await self._fail(
                attempt,
                ExecutionErrorCode.INTERNAL_FAILURE,
                ambiguous=False,
                stop=stop_heartbeat,
                heartbeat=heartbeat,
            )
        finally:
            if not heartbeat_stopped:
                await self._quiet_stop_heartbeat(stop_heartbeat, heartbeat)
            source = None
            custom_reference = None
            provider_image = None
            normalized = None
            payload = None

    async def _resolve_intent(
        self,
        attempt: ClaimedAttempt,
    ) -> tuple[GenerationIntent, bytes | None]:
        if attempt.intent_kind is IntentKind.PALETTE:
            if attempt.palette_color_id is None:
                raise ValueError("invalid palette snapshot")
            try:
                choice = get_palette_choice(attempt.palette_color_id)
            except PaletteLookupError:
                raise ValueError("invalid palette snapshot") from None
            if not isinstance(choice, PaletteChoice):
                raise ValueError("invalid palette snapshot")
            return BuiltInColorIntent(choice), None
        if attempt.intent_kind is IntentKind.SURPRISE:
            choice = get_palette_choice("surprise_me")
            if not isinstance(choice, SurpriseChoice):
                raise ValueError("invalid surprise snapshot")
            return SurpriseIntent(choice), None

        version = await self._transaction(
            lambda session: self._repository.resolve_custom_version(session, attempt)
        )
        if version is None:
            raise CustomReferenceError
        try:
            reference = await asyncio.to_thread(
                self._storage.read,
                version.object_key,
                version.sha256,
            )
            validated = validate_image_bytes(
                reference,
                max_width=self._settings.custom_color_max_side_px,
                max_height=self._settings.custom_color_max_side_px,
                max_pixels=self._settings.custom_color_max_pixels,
            )
        except (OSError, ValueError, ImageValidationError):
            raise CustomReferenceError from None
        if (
            validated.image_format != "png"
            or len(reference) != version.byte_size
            or validated.width != version.width
            or validated.height != version.height
            or version.id != attempt.custom_color_version_id
            or version.sha256 != attempt.custom_color_sha256
        ):
            raise CustomReferenceError
        return custom_intent(version), reference

    async def _heartbeat_loop(
        self,
        attempt: ClaimedAttempt,
        stop: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self._settings.job_heartbeat_seconds,
                )
            except TimeoutError:
                await self._transaction(
                    lambda session: self._repository.heartbeat(
                        session,
                        attempt,
                        now=self._clock(),
                        lease_seconds=self._settings.job_lease_seconds,
                    )
                )

    async def _fail(
        self,
        attempt: ClaimedAttempt,
        code: ExecutionErrorCode,
        *,
        ambiguous: bool,
        stop: asyncio.Event,
        heartbeat: asyncio.Task[None],
    ) -> WorkerOutcome:
        await self._stop_heartbeat(stop, heartbeat)
        await self._transaction(
            lambda session: self._repository.mark_failed(
                session,
                attempt,
                code,
                summary=_ERROR_SUMMARY[code],
                ambiguous=ambiguous,
                now=self._clock(),
            )
        )
        await send_recovery(
            self._sender,
            chat_id=attempt.chat_id,
            source_message_id=attempt.source_message_id,
            code=code,
        )
        return WorkerOutcome(job_id=attempt.job_id, error_code=code)

    async def _transaction(
        self,
        operation: Callable[[AsyncSession], Awaitable[T]],
    ) -> T:
        async with self._sessions() as session:
            try:
                result = await operation(session)
                await session.commit()
                return result
            except BaseException:
                await session.rollback()
                raise

    @staticmethod
    async def _stop_heartbeat(
        stop: asyncio.Event,
        heartbeat: asyncio.Task[None],
    ) -> None:
        stop.set()
        await heartbeat

    @staticmethod
    async def _quiet_stop_heartbeat(
        stop: asyncio.Event,
        heartbeat: asyncio.Task[None],
    ) -> None:
        stop.set()
        if not heartbeat.done():
            heartbeat.cancel()
        with suppress(asyncio.CancelledError, LostLeaseError):
            await heartbeat
