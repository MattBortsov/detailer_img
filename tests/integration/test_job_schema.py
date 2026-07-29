"""PostgreSQL invariants for durable metadata-only generation jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, LargeBinary, String, Text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from car_wrap.db.models import (
    CustomColor,
    CustomColorVersion,
    GenerationJob,
    JobOutbox,
)

pytestmark = pytest.mark.postgresql


def job_values(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "id": uuid4(),
        "telegram_user_id": 1001,
        "client_submission_uuid": uuid4(),
        "chat_id": 1001,
        "source_message_id": 17,
        "telegram_file_id": "telegram-file-reference",
        "telegram_file_unique_id": "telegram-unique-reference",
        "source_media_kind": "photo",
        "source_mime_type": "image/jpeg",
        "source_byte_size": 1024,
        "source_width": 1200,
        "source_height": 800,
        "intent_kind": "palette",
        "palette_color_id": "satin-black",
        "custom_color_version_id": None,
        "custom_color_sha256": None,
        "intent_display_name": "Чёрный сатиновый",
        "image_model": "x-ai/grok-imagine-image-quality",
        "prompt_revision": "vehicle-wrap-v1",
        "status": "queued",
        "error_code": None,
        "error_summary": None,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return values


async def create_custom_version(database_engine: AsyncEngine) -> UUID:
    color_id = uuid4()
    version_id = uuid4()
    async with database_engine.begin() as connection:
        await connection.execute(
            CustomColor.__table__.insert().values(
                id=color_id,
                telegram_user_id=2002,
                display_name="Bronze",
                status="approved",
                current_version=1,
                approved_at=datetime.now(UTC),
            )
        )
        await connection.execute(
            CustomColorVersion.__table__.insert().values(
                id=version_id,
                custom_color_id=color_id,
                version=1,
                object_key="aa/bb/" + "c" * 32 + ".png",
                sha256="a" * 64,
                byte_size=128,
                width=64,
                height=64,
            )
        )
    return version_id


def test_job_and_outbox_models_are_bounded_metadata_only() -> None:
    assert {"generation_jobs", "job_outbox"} <= set(GenerationJob.metadata.tables)
    assert set(JobOutbox.__table__.columns.keys()) == {
        "job_id",
        "created_at",
        "published_at",
        "publish_attempts",
        "last_attempt_at",
    }
    for column in (*GenerationJob.__table__.columns, *JobOutbox.__table__.columns):
        assert not isinstance(column.type, (JSON, LargeBinary, Text))
        if isinstance(column.type, String):
            assert column.type.length is not None


@pytest.mark.asyncio
async def test_schema_accepts_exactly_the_three_tagged_intents(
    database_engine: AsyncEngine,
) -> None:
    custom_version_id = await create_custom_version(database_engine)
    rows = (
        job_values(),
        job_values(
            telegram_user_id=1002,
            intent_kind="surprise",
            palette_color_id=None,
            intent_display_name="Удиви меня",
        ),
        job_values(
            telegram_user_id=1003,
            intent_kind="custom",
            palette_color_id=None,
            custom_color_version_id=custom_version_id,
            custom_color_sha256="a" * 64,
            intent_display_name="Bronze",
        ),
    )
    async with database_engine.begin() as connection:
        for row in rows:
            await connection.execute(GenerationJob.__table__.insert().values(**row))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    (
        {"telegram_user_id": 0},
        {"source_message_id": 0},
        {"source_byte_size": 0},
        {"source_width": 0},
        {"source_height": 0},
        {"source_media_kind": "video"},
        {"source_mime_type": "image/gif"},
        {"status": "accepted"},
        {"intent_kind": "surprise"},
        {"intent_kind": "palette", "palette_color_id": None},
        {
            "intent_kind": "custom",
            "palette_color_id": None,
            "custom_color_version_id": None,
            "custom_color_sha256": "a" * 64,
        },
        {"custom_color_sha256": "not-a-digest"},
        {"status": "queued", "error_code": "provider_failed"},
        {"status": "failed", "error_code": None},
    ),
)
async def test_job_constraints_reject_invalid_rows(
    database_engine: AsyncEngine,
    overrides: dict[str, object],
) -> None:
    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                GenerationJob.__table__.insert().values(**job_values(**overrides))
            )


@pytest.mark.asyncio
async def test_idempotency_and_one_outbox_row_are_database_enforced(
    database_engine: AsyncEngine,
) -> None:
    submission_id = uuid4()
    first = job_values(client_submission_uuid=submission_id)
    async with database_engine.begin() as connection:
        await connection.execute(GenerationJob.__table__.insert().values(**first))
        await connection.execute(
            JobOutbox.__table__.insert().values(job_id=first["id"])
        )

    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                GenerationJob.__table__.insert().values(
                    **job_values(client_submission_uuid=submission_id)
                )
            )

    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                JobOutbox.__table__.insert().values(job_id=first["id"])
            )


@pytest.mark.asyncio
async def test_outbox_attempt_state_is_consistent(
    database_engine: AsyncEngine,
) -> None:
    row = job_values()
    async with database_engine.begin() as connection:
        await connection.execute(GenerationJob.__table__.insert().values(**row))

    for values in (
        {"job_id": row["id"], "publish_attempts": -1},
        {"job_id": row["id"], "publish_attempts": 1, "last_attempt_at": None},
        {
            "job_id": row["id"],
            "published_at": datetime.now(UTC),
            "publish_attempts": 0,
            "last_attempt_at": None,
        },
    ):
        with pytest.raises(IntegrityError):
            async with database_engine.begin() as connection:
                await connection.execute(JobOutbox.__table__.insert().values(**values))


def test_phase3_migration_has_no_arbitrary_private_payload() -> None:
    migration = (
        Path(__file__).parents[2] / "alembic/versions/0003_generation_jobs.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "LargeBinary",
        "sa.JSON",
        "sa.Text",
        "payload",
        "base64",
        "data_url",
        "download_url",
        "authorization",
        "init_data",
        "provider_body",
    )
    assert not any(fragment in migration for fragment in forbidden)
