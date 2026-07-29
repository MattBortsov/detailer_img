"""Static and runtime canaries for the private generation boundary."""

from __future__ import annotations

import inspect
from pathlib import Path
from uuid import uuid4

import yaml

from car_wrap.bot.media import DownloadedMedia
from car_wrap.generation.provider import ProviderImage
from car_wrap.generation.result import TelegramPhoto
from car_wrap.jobs.contracts import ProviderReceipt
from car_wrap.worker.main import WorkerCoordinator
from car_wrap.worker.service import WorkerOutcome

ROOT = Path(__file__).parents[2]


def test_media_objects_and_task_outcomes_redact_byte_canaries() -> None:
    canary = b"PRIVATE_IMAGE_BYTES_CANARY"
    receipt = ProviderReceipt(
        provider_name="openrouter",
        request_id=None,
        status_code=200,
        latency_ms=1,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        cost_usd=None,
        output_byte_count=len(canary),
        output_width=300,
        output_height=200,
        output_format="png",
        output_sha256="a" * 64,
    )
    values = (
        DownloadedMedia(
            data=canary,
            mime_type="image/jpeg",
            byte_size=len(canary),
            width=300,
            height=200,
        ),
        ProviderImage(data=canary, receipt=receipt),
        TelegramPhoto(
            data=canary,
            width=300,
            height=200,
            byte_count=len(canary),
            image_format="jpeg",
            sha256="b" * 64,
        ),
        WorkerOutcome(job_id=uuid4(), error_code=None),
    )
    assert all(canary.decode() not in repr(value) for value in values)


def test_worker_has_no_raw_exception_or_media_persistence_path() -> None:
    coordinator_source = inspect.getsource(WorkerCoordinator)
    worker_source = (ROOT / "src/car_wrap/worker/main.py").read_text()
    service_source = (ROOT / "src/car_wrap/worker/service.py").read_text()
    generation_source = "\n".join(
        (
            service_source,
            (ROOT / "src/car_wrap/generation/provider.py").read_text(),
            (ROOT / "src/car_wrap/generation/result.py").read_text(),
        )
    )
    assert "logger.exception" not in coordinator_source
    assert "str(error)" not in coordinator_source
    assert "NamedTemporaryFile" not in generation_source
    assert "mkstemp" not in generation_source
    assert ".write_bytes(" not in generation_source
    assert "intent_display_name" not in inspect.getsource(
        __import__(
            "car_wrap.generation.openrouter",
            fromlist=["build_generation_payload"],
        ).build_generation_payload
    )
    assert "celery" not in worker_source.lower()
    assert "langchain" not in worker_source.lower()
    assert "langgraph" not in worker_source.lower()


def test_compose_worker_persists_only_read_only_custom_references() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    worker = compose["services"]["worker"]
    assert worker["command"] == ["python", "-m", "car_wrap.worker.main"]
    assert worker["read_only"] is True
    assert worker["mem_limit"] == "768m"
    assert worker["tmpfs"] == ["/tmp:size=32m,mode=1777"]  # noqa: S108
    assert worker["volumes"] == ["custom_color_data:/var/lib/car-wrap/custom-colors:ro"]
    assert set(worker["networks"]) == {"backend", "edge"}
