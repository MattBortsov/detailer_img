"""Accepted palette job through generation to Telegram receipt."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from car_wrap.custom_colors.repository import CustomColorRepository
from car_wrap.db.models import ActiveSource, GenerationJob
from car_wrap.jobs.repository import JobRepository
from car_wrap.jobs.service import JobAcceptanceService
from car_wrap.jobs.worker_repository import WorkerRepository
from car_wrap.worker.service import GenerationWorkerService
from tests.integration.test_worker_lifecycle import (
    NOW,
    SOURCE,
    Downloader,
    Provider,
    Storage,
    _settings,
)

pytestmark = [pytest.mark.postgresql, pytest.mark.asyncio]


class RecordingSender:
    def __init__(self) -> None:
        self.id = 1
        self.photo_call: dict[str, Any] | None = None

    async def send_photo(self, **kwargs: Any) -> Any:
        self.photo_call = kwargs
        return SimpleNamespace(message_id=99, chat=SimpleNamespace(id=1001))

    async def send_message(self, **kwargs: Any) -> Any:
        return SimpleNamespace(message_id=100)

    async def send_chat_action(self, **kwargs: Any) -> Any:
        return True


async def test_accepted_job_becomes_exact_reply_and_durable_receipt(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(
            ActiveSource(
                telegram_user_id=1001,
                chat_id=1001,
                source_message_id=17,
                telegram_file_id="source-file-id",
                telegram_file_unique_id="source-unique-id",
                media_kind="photo",
                mime_type="image/jpeg",
                byte_size=len(SOURCE),
                width=300,
                height=260,
                accepted_at=NOW,
                updated_at=NOW,
            )
        )
        await session.commit()
    custom_colors = CustomColorRepository(quota=20)
    acceptance = JobAcceptanceService(
        repository=JobRepository(),
        custom_colors=custom_colors,
        image_model="x-ai/grok-imagine-image-quality",
        prompt_revision="vehicle-wrap-v1",
        max_active=1,
        max_recent=10,
        window_seconds=3600,
        clock=lambda: NOW,
    )
    async with sessions() as session:
        accepted = await acceptance.accept(
            session,
            user_id=1001,
            color_id="charcoal",
            submission_uuid=uuid4(),
        )

    repository = WorkerRepository(custom_colors)
    async with sessions() as session:
        attempt = await repository.claim(
            session,
            worker_id="worker-1",
            now=NOW,
            lease_seconds=300,
            job_id=accepted.job_id,
        )
        await session.commit()
    assert attempt is not None
    sender = RecordingSender()
    provider = Provider()
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
    assert provider.calls == 1
    assert sender.photo_call is not None
    assert sender.photo_call["caption"] == (
        "✅ Ваше фото готово!\n\n"
        "Результат работы @CarWrapBot\n\n"
        "<i>Это AI-визуализация. Реальный цвет может отличаться "
        "в зависимости от вашего экрана.</i>"
    )
    assert sender.photo_call["parse_mode"] == "HTML"
    buttons = sender.photo_call["reply_markup"].inline_keyboard[0]
    assert [button.text for button in buttons] == ["Новая генерация", "Меню"]
    reply = sender.photo_call["reply_parameters"]
    assert (sender.photo_call["chat_id"], reply.message_id) == (1001, 17)
    assert reply.allow_sending_without_reply is False
    async with sessions() as session:
        job = await session.get(GenerationJob, accepted.job_id)
    assert job is not None
    assert (job.status, job.result_message_id) == ("succeeded", 99)
