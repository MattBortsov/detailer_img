"""Closed metadata-only execution contracts."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from car_wrap.jobs.contracts import (
    AttemptState,
    ClaimedAttempt,
    DeliveryReceipt,
    ExecutionErrorCode,
    IntentKind,
    ProviderReceipt,
)


def test_execution_states_and_errors_are_closed() -> None:
    assert {item.value for item in AttemptState} == {
        "claimed",
        "source_ready",
        "provider_started",
        "provider_succeeded",
        "delivering",
        "succeeded",
        "failed",
        "ambiguous",
    }
    assert {item.value for item in ExecutionErrorCode} == {
        "source_unavailable",
        "source_changed",
        "custom_reference_unavailable",
        "provider_unavailable",
        "provider_rejected",
        "provider_invalid_response",
        "provider_ambiguous",
        "result_invalid",
        "delivery_unavailable",
        "delivery_ambiguous",
        "internal_failure",
    }


def test_execution_contracts_contain_allowlisted_metadata_only() -> None:
    forbidden = {"bytes", "base64", "url", "authorization", "body", "payload"}
    names = {
        field.name.lower()
        for contract in (ClaimedAttempt, ProviderReceipt, DeliveryReceipt)
        for field in fields(contract)
    }
    assert not any(token in name for token in forbidden for name in names)

    claimed = ClaimedAttempt(
        job_id=uuid4(),
        attempt_id=uuid4(),
        attempt_number=1,
        worker_id="worker-1",
        lease_expires_at=datetime.now(UTC),
        telegram_user_id=10,
        chat_id=10,
        source_message_id=12,
        telegram_file_id="opaque-file-id",
        source_media_kind="photo",
        source_mime_type="image/jpeg",
        source_byte_size=100,
        source_width=1000,
        source_height=700,
        intent_kind=IntentKind.PALETTE,
        intent_display_name="Чёрный сатиновый",
        palette_color_id="charcoal",
        custom_color_version_id=None,
        custom_color_sha256=None,
        image_model="x-ai/grok-imagine-image-quality",
        prompt_revision="vehicle-wrap-v1",
    )
    receipt = ProviderReceipt(
        provider_name="openrouter",
        request_id="req-1",
        status_code=200,
        latency_ms=1000,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost_usd=Decimal("0.06"),
        output_byte_count=1024,
        output_width=1024,
        output_height=768,
        output_format="png",
        output_sha256="a" * 64,
    )
    rendered = repr((claimed, receipt, DeliveryReceipt(chat_id=10, message_id=20)))
    for canary in ("data:image", "Bearer ", "https://", "provider_body"):
        assert canary not in rendered
