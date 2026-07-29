"""One-job worker orchestration contracts."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from PIL import Image

from car_wrap.config import AppSettings
from car_wrap.db.models import CustomColorVersion
from car_wrap.generation.provider import (
    ProviderFailure,
    ProviderFailureKind,
    ProviderImage,
)
from car_wrap.jobs.contracts import (
    ClaimedAttempt,
    DeliveryReceipt,
    ExecutionErrorCode,
    IntentKind,
    ProviderReceipt,
)
from car_wrap.worker.service import GenerationWorkerService

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)


def _image(image_format: str, color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (300, 260), color).save(buffer, format=image_format)
    return buffer.getvalue()


SOURCE = _image("JPEG", (20, 30, 40))
PROVIDER_OUTPUT = _image("PNG", (50, 60, 70))
CUSTOM_REFERENCE = _image("PNG", (180, 120, 40))


def _settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "database_url": "postgresql+psycopg://user:pass@db/test",
            "bot_token": "test-token",
            "bot_username": "CarWrapBot",
            "mini_app_url": "https://wrap.example.com/app",
            "job_heartbeat_seconds": 30,
            "job_lease_seconds": 300,
        }
    )


def _attempt(
    kind: IntentKind = IntentKind.PALETTE,
    *,
    custom_version_id: UUID | None = None,
) -> ClaimedAttempt:
    digest = hashlib.sha256(CUSTOM_REFERENCE).hexdigest()
    return ClaimedAttempt(
        job_id=uuid4(),
        attempt_id=uuid4(),
        attempt_number=1,
        worker_id="worker-1",
        lease_expires_at=NOW + timedelta(minutes=5),
        telegram_user_id=10,
        chat_id=10,
        source_message_id=20,
        telegram_file_id="source-file-id",
        source_media_kind="photo",
        source_mime_type="image/jpeg",
        source_byte_size=len(SOURCE),
        source_width=300,
        source_height=260,
        intent_kind=kind,
        intent_display_name=(
            "Мой бронзовый" if kind is IntentKind.CUSTOM else "Графитовый"
        ),
        palette_color_id="charcoal" if kind is IntentKind.PALETTE else None,
        custom_color_version_id=(
            custom_version_id if kind is IntentKind.CUSTOM else None
        ),
        custom_color_sha256=digest if kind is IntentKind.CUSTOM else None,
        image_model="x-ai/grok-imagine-image-quality",
        prompt_revision="vehicle-wrap-v1",
    )


class Session:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class SessionContext:
    async def __aenter__(self) -> Session:
        return Session()

    async def __aexit__(self, *args: object) -> None:
        return None


class Repository:
    def __init__(self, version: CustomColorVersion | None = None) -> None:
        self.events: list[object] = []
        self.version = version

    async def heartbeat(
        self, session: object, attempt: ClaimedAttempt, **_: Any
    ) -> None:
        self.events.append("heartbeat")

    async def mark_source_ready(
        self, session: object, attempt: ClaimedAttempt, **_: Any
    ) -> None:
        self.events.append("source_ready")

    async def mark_provider_started(
        self, session: object, attempt: ClaimedAttempt, **_: Any
    ) -> None:
        self.events.append("provider_started")

    async def record_safe_preupload_retry(
        self, session: object, attempt: ClaimedAttempt, **_: Any
    ) -> None:
        self.events.append("safe_retry")

    async def mark_provider_succeeded(
        self,
        session: object,
        attempt: ClaimedAttempt,
        receipt: ProviderReceipt,
        **_: Any,
    ) -> None:
        self.events.append(("provider_succeeded", receipt.output_sha256))

    async def mark_delivering(
        self, session: object, attempt: ClaimedAttempt, **_: Any
    ) -> None:
        self.events.append("delivering")

    async def mark_succeeded(
        self,
        session: object,
        attempt: ClaimedAttempt,
        receipt: DeliveryReceipt,
        **_: Any,
    ) -> None:
        self.events.append(("succeeded", receipt.message_id))

    async def mark_failed(
        self,
        session: object,
        attempt: ClaimedAttempt,
        code: ExecutionErrorCode,
        **kwargs: Any,
    ) -> None:
        self.events.append(("failed", code, kwargs["ambiguous"]))

    async def resolve_custom_version(
        self,
        session: object,
        attempt: ClaimedAttempt,
    ) -> CustomColorVersion | None:
        return self.version


class Downloader:
    async def download(self, file: str, destination: Any) -> object:
        assert file == "source-file-id"
        destination.write(SOURCE)
        return destination


class Storage:
    def read(self, key: str, expected_sha256: str) -> bytes:
        assert key.endswith(".png")
        assert expected_sha256 == hashlib.sha256(CUSTOM_REFERENCE).hexdigest()
        return CUSTOM_REFERENCE


def _receipt() -> ProviderReceipt:
    return ProviderReceipt(
        provider_name="openrouter",
        request_id="req-safe",
        status_code=200,
        latency_ms=100,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        cost_usd=None,
        output_byte_count=len(PROVIDER_OUTPUT),
        output_width=300,
        output_height=260,
        output_format="png",
        output_sha256=hashlib.sha256(PROVIDER_OUTPUT).hexdigest(),
    )


class Provider:
    def __init__(self, failure: ProviderFailureKind | None = None) -> None:
        self.failure = failure
        self.payloads: list[dict[str, Any]] = []

    async def generate(
        self,
        payload: dict[str, Any],
        *,
        on_safe_retry: Any = None,
    ) -> ProviderImage:
        self.payloads.append(payload)
        if self.failure is not None:
            raise ProviderFailure(self.failure)
        return ProviderImage(data=PROVIDER_OUTPUT, receipt=_receipt())


class Sender:
    def __init__(self) -> None:
        self.id = 1
        self.photos = 0
        self.messages: list[str] = []
        self.actions: list[str] = []

    async def send_photo(self, **kwargs: Any) -> Any:
        self.photos += 1
        return SimpleNamespace(message_id=30, chat=SimpleNamespace(id=10))

    async def send_message(self, **kwargs: Any) -> Any:
        self.messages.append(kwargs["text"])
        return SimpleNamespace(message_id=31)

    async def send_chat_action(self, **kwargs: Any) -> Any:
        self.actions.append(kwargs["action"])
        return True


def _service(
    repository: Repository,
    provider: Provider,
    sender: Sender,
) -> GenerationWorkerService:
    return GenerationWorkerService(
        session_factory=SessionContext,
        repository=repository,
        downloader=Downloader(),
        storage=Storage(),
        provider=provider,
        sender=sender,
        settings=_settings(),
        clock=lambda: NOW,
    )


async def test_palette_success_has_durable_side_effect_order() -> None:
    repository = Repository()
    provider = Provider()
    sender = Sender()

    outcome = await _service(repository, provider, sender).execute(_attempt())

    assert outcome.error_code is None
    assert repository.events == [
        "source_ready",
        "provider_started",
        ("provider_succeeded", hashlib.sha256(PROVIDER_OUTPUT).hexdigest()),
        "delivering",
        ("succeeded", 30),
    ]
    assert len(provider.payloads) == 1
    assert len(provider.payloads[0]["input_references"]) == 1
    assert "Графитовый" not in repr(provider.payloads[0])
    assert sender.photos == 1
    assert sender.messages == ["🎨 Генерация запущена. Результат придёт в этот чат."]
    assert sender.actions == ["upload_photo"]


async def test_custom_uses_exact_digest_verified_second_reference() -> None:
    version_id = uuid4()
    version = CustomColorVersion(
        id=version_id,
        custom_color_id=uuid4(),
        version=2,
        object_key="aa/bb/" + "c" * 32 + ".png",
        sha256=hashlib.sha256(CUSTOM_REFERENCE).hexdigest(),
        byte_size=len(CUSTOM_REFERENCE),
        width=300,
        height=260,
        retain_count=1,
        created_at=NOW,
    )
    repository = Repository(version)
    provider = Provider()
    sender = Sender()

    outcome = await _service(repository, provider, sender).execute(
        _attempt(IntentKind.CUSTOM, custom_version_id=version_id)
    )

    assert outcome.error_code is None
    assert len(provider.payloads[0]["input_references"]) == 2
    assert provider.payloads[0]["input_references"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (ProviderFailureKind.UNAVAILABLE, ExecutionErrorCode.PROVIDER_UNAVAILABLE),
        (ProviderFailureKind.REJECTED, ExecutionErrorCode.PROVIDER_REJECTED),
        (
            ProviderFailureKind.INVALID_RESPONSE,
            ExecutionErrorCode.PROVIDER_INVALID_RESPONSE,
        ),
        (ProviderFailureKind.AMBIGUOUS, ExecutionErrorCode.PROVIDER_AMBIGUOUS),
    ],
)
async def test_provider_failure_is_terminal_without_delivery(
    kind: ProviderFailureKind,
    expected: ExecutionErrorCode,
) -> None:
    repository = Repository()
    provider = Provider(kind)
    sender = Sender()

    outcome = await _service(repository, provider, sender).execute(_attempt())

    assert outcome.error_code is expected
    assert sender.photos == 0
    assert len(provider.payloads) == 1
    assert repository.events[-1] == (
        "failed",
        expected,
        kind is ProviderFailureKind.AMBIGUOUS,
    )
    assert len(sender.messages) == 2
    assert repr(outcome).find(SOURCE[:20].hex()) == -1
