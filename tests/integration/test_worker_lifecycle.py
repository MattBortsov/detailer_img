"""Real PostgreSQL worker-service terminal lifecycle checks."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from car_wrap.config import AppSettings
from car_wrap.custom_colors.repository import CustomColorRepository
from car_wrap.db.models import (
    CustomColor,
    CustomColorVersion,
    GenerationAttempt,
    GenerationJob,
)
from car_wrap.generation.provider import ProviderImage
from car_wrap.jobs.contracts import ProviderReceipt
from car_wrap.jobs.worker_repository import WorkerRepository
from car_wrap.worker.service import GenerationWorkerService

pytestmark = [pytest.mark.postgresql, pytest.mark.asyncio]
NOW = datetime(2026, 7, 29, 13, tzinfo=UTC)


def _image(image_format: str, color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (300, 260), color).save(buffer, format=image_format)
    return buffer.getvalue()


SOURCE = _image("JPEG", (10, 20, 30))
OUTPUT = _image("PNG", (40, 50, 60))
REFERENCE = _image("PNG", (150, 100, 40))


def _settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "database_url": "postgresql+psycopg://user:pass@db/test",
            "bot_token": "test-token",
            "bot_username": "CarWrapBot",
            "mini_app_url": "https://wrap.example.com/app",
        }
    )


class Downloader:
    async def download(self, file: str, destination: Any) -> object:
        destination.write(SOURCE)
        return destination


class Storage:
    def read(self, key: str, expected_sha256: str) -> bytes:
        assert expected_sha256 == hashlib.sha256(REFERENCE).hexdigest()
        return REFERENCE


class Provider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, payload: dict[str, Any], **_: Any) -> ProviderImage:
        self.calls += 1
        return ProviderImage(
            data=OUTPUT,
            receipt=ProviderReceipt(
                provider_name="openrouter",
                request_id="req-1",
                status_code=200,
                latency_ms=100,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                cost_usd=None,
                output_byte_count=len(OUTPUT),
                output_width=300,
                output_height=260,
                output_format="png",
                output_sha256=hashlib.sha256(OUTPUT).hexdigest(),
            ),
        )


class Sender:
    def __init__(self) -> None:
        self.id = 1
        self.photos = 0

    async def send_photo(self, **kwargs: Any) -> Any:
        self.photos += 1
        return SimpleNamespace(
            message_id=99,
            chat=SimpleNamespace(id=kwargs["chat_id"]),
        )

    async def send_message(self, **kwargs: Any) -> Any:
        return SimpleNamespace(message_id=100)

    async def send_chat_action(self, **kwargs: Any) -> Any:
        return True


async def _custom_version(
    sessions: async_sessionmaker[AsyncSession],
) -> UUID:
    color_id = uuid4()
    version_id = uuid4()
    async with sessions() as session:
        session.add(
            CustomColor(
                id=color_id,
                telegram_user_id=2002,
                display_name="Bronze",
                status="approved",
                current_version=1,
                approved_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            CustomColorVersion(
                id=version_id,
                custom_color_id=color_id,
                version=1,
                object_key="aa/bb/" + "c" * 32 + ".png",
                sha256=hashlib.sha256(REFERENCE).hexdigest(),
                byte_size=len(REFERENCE),
                width=300,
                height=260,
                retain_count=1,
                created_at=NOW,
            )
        )
        await session.commit()
    return version_id


@pytest.mark.parametrize("intent_kind", ["palette", "surprise", "custom"])
async def test_all_intents_finish_only_after_telegram_receipt(
    database_engine: AsyncEngine,
    intent_kind: str,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await _custom_version(sessions) if intent_kind == "custom" else None
    job = GenerationJob(
        id=uuid4(),
        telegram_user_id=1001,
        client_submission_uuid=uuid4(),
        chat_id=1001,
        source_message_id=17,
        telegram_file_id="source-file-id",
        telegram_file_unique_id="source-unique-id",
        source_media_kind="photo",
        source_mime_type="image/jpeg",
        source_byte_size=len(SOURCE),
        source_width=300,
        source_height=260,
        intent_kind=intent_kind,
        palette_color_id="charcoal" if intent_kind == "palette" else None,
        custom_color_version_id=version_id,
        custom_color_sha256=(
            hashlib.sha256(REFERENCE).hexdigest() if intent_kind == "custom" else None
        ),
        intent_display_name=(
            "Удиви меня"
            if intent_kind == "surprise"
            else ("Bronze" if intent_kind == "custom" else "Графитовый")
        ),
        image_model="x-ai/grok-imagine-image-quality",
        prompt_revision="vehicle-wrap-v1",
        status="queued",
        created_at=NOW,
        updated_at=NOW,
    )
    async with sessions() as session:
        session.add(job)
        await session.commit()

    repository = WorkerRepository(CustomColorRepository(quota=20))
    async with sessions() as session:
        attempt = await repository.claim(
            session,
            worker_id="worker-1",
            now=NOW,
            lease_seconds=300,
            job_id=job.id,
        )
        await session.commit()
    assert attempt is not None
    provider = Provider()
    sender = Sender()
    service = GenerationWorkerService(
        session_factory=sessions,
        repository=repository,
        downloader=Downloader(),
        storage=Storage(),
        provider=provider,
        sender=sender,
        settings=_settings(),
        clock=lambda: NOW,
    )

    outcome = await service.execute(attempt)

    assert outcome.error_code is None
    assert (provider.calls, sender.photos) == (1, 1)
    async with sessions() as session:
        stored = await session.get(GenerationJob, job.id)
        execution = await session.get(GenerationAttempt, attempt.attempt_id)
        version = (
            await session.get(CustomColorVersion, version_id)
            if version_id is not None
            else None
        )
    assert stored is not None and execution is not None
    assert (stored.status, stored.result_message_id, execution.state) == (
        "succeeded",
        99,
        "succeeded",
    )
    assert stored.lease_owner is None
    if version is not None:
        assert version.retain_count == 0
        assert stored.custom_reference_released_at is not None
