"""Closed metadata-only contracts for durable job acceptance."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from car_wrap.db.models import ActiveSource

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class IntentKind(StrEnum):
    PALETTE = "palette"
    CUSTOM = "custom"
    SURPRISE = "surprise"


class AcceptanceErrorCode(StrEnum):
    NO_SOURCE = "no_source"
    INVALID_SELECTION = "invalid_selection"
    ACTIVE_LIMIT = "active_limit"
    RECENT_LIMIT = "recent_limit"


class AttemptState(StrEnum):
    CLAIMED = "claimed"
    SOURCE_READY = "source_ready"
    PROVIDER_STARTED = "provider_started"
    PROVIDER_SUCCEEDED = "provider_succeeded"
    DELIVERING = "delivering"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class ExecutionErrorCode(StrEnum):
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_CHANGED = "source_changed"
    CUSTOM_REFERENCE_UNAVAILABLE = "custom_reference_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_REJECTED = "provider_rejected"
    PROVIDER_INVALID_RESPONSE = "provider_invalid_response"
    PROVIDER_AMBIGUOUS = "provider_ambiguous"
    RESULT_INVALID = "result_invalid"
    DELIVERY_UNAVAILABLE = "delivery_unavailable"
    DELIVERY_AMBIGUOUS = "delivery_ambiguous"
    INTERNAL_FAILURE = "internal_failure"


class JobAcceptanceError(ValueError):
    def __init__(self, code: AcceptanceErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    chat_id: int
    message_id: int
    file_id: str
    file_unique_id: str
    media_kind: str
    mime_type: str
    byte_size: int
    width: int
    height: int

    @classmethod
    def from_active_source(cls, source: ActiveSource) -> SourceSnapshot:
        return cls(
            chat_id=source.chat_id,
            message_id=source.source_message_id,
            file_id=source.telegram_file_id,
            file_unique_id=source.telegram_file_unique_id,
            media_kind=source.media_kind,
            mime_type=source.mime_type,
            byte_size=source.byte_size,
            width=source.width,
            height=source.height,
        )


@dataclass(frozen=True, slots=True)
class IntentSnapshot:
    kind: IntentKind
    display_name: str
    palette_color_id: str | None = None
    custom_color_version_id: UUID | None = None
    custom_color_sha256: str | None = None

    def __post_init__(self) -> None:
        palette = self.palette_color_id is not None
        custom = (
            self.custom_color_version_id is not None
            and self.custom_color_sha256 is not None
        )
        valid = (
            (self.kind is IntentKind.PALETTE and palette and not custom)
            or (
                self.kind is IntentKind.CUSTOM
                and custom
                and not palette
                and bool(_SHA256.fullmatch(self.custom_color_sha256 or ""))
            )
            or (
                self.kind is IntentKind.SURPRISE
                and not palette
                and not custom
                and self.custom_color_sha256 is None
                and self.custom_color_version_id is None
            )
        )
        if not valid or not 1 <= len(self.display_name) <= 40:
            raise ValueError("invalid job intent snapshot")


@dataclass(frozen=True, slots=True)
class AcceptedJob:
    job_id: UUID
    status: JobStatus


@dataclass(frozen=True, slots=True)
class ClaimedAttempt:
    job_id: UUID
    attempt_id: UUID
    attempt_number: int
    worker_id: str
    lease_expires_at: datetime
    telegram_user_id: int
    chat_id: int
    source_message_id: int
    telegram_file_id: str
    source_media_kind: str
    source_mime_type: str
    source_byte_size: int
    source_width: int
    source_height: int
    intent_kind: IntentKind
    intent_display_name: str
    palette_color_id: str | None
    custom_color_version_id: UUID | None
    custom_color_sha256: str | None
    image_model: str
    prompt_revision: str


@dataclass(frozen=True, slots=True)
class ProviderReceipt:
    provider_name: str
    request_id: str | None
    status_code: int
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cost_usd: Decimal | None
    output_byte_count: int
    output_width: int
    output_height: int
    output_format: str
    output_sha256: str


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    chat_id: int
    message_id: int
