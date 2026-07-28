"""Complete custom-color lifecycle through real API and persistence boundaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image, PngImagePlugin
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from car_wrap.api.app import create_app
from car_wrap.api.dependencies import (
    CurrentMiniAppSession,
    require_mini_app_session,
)
from car_wrap.config import AppSettings
from car_wrap.custom_colors.media import MediaPolicy, normalize_reference
from car_wrap.custom_colors.moderation import (
    ModerationDisposition,
    ModerationResult,
)
from car_wrap.custom_colors.repository import CustomColorRepository
from car_wrap.custom_colors.service import CustomColorService
from car_wrap.custom_colors.storage import FilesystemPrivateStorage
from car_wrap.db.models import ActiveSource, CustomColorVersion
from car_wrap.generation.contracts import custom_intent
from car_wrap.generation.openrouter import build_generation_payload
from car_wrap.palette import custom_selection_id

pytestmark = [pytest.mark.postgresql, pytest.mark.asyncio]
NOW = datetime(2026, 7, 28, 14, tzinfo=UTC)


class CleanScanner:
    def scan(self, data: bytes) -> None:
        assert data


def source_png() -> bytes:
    image = Image.new("RGB", (96, 64), "#A36D3E")
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private-canary", "must-not-survive")
    output = BytesIO()
    image.save(output, format="PNG", pnginfo=metadata)
    return output.getvalue() + b"trailing-original-canary"


def settings(database_url: str, storage_root: Path) -> AppSettings:
    return AppSettings.model_validate(
        {
            "database_url": database_url,
            "bot_token": "token",
            "bot_username": "CarWrapBot",
            "mini_app_url": "https://wrap.example.com/app",
            "custom_color_storage_root": storage_root,
            "admin_telegram_user_ids": (9009,),
        }
    )


async def test_upload_select_generate_retain_delete_release_lifecycle(
    database_engine: AsyncEngine,
    test_database_url: str,
    tmp_path: Path,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    storage = FilesystemPrivateStorage(
        tmp_path / "private-colors",
        max_object_bytes=8 * 1024 * 1024,
    )
    repository = CustomColorRepository(quota=20)
    policy = MediaPolicy(decode_timeout_seconds=5)
    moderation_disposition = {"value": ModerationDisposition.APPROVED}

    async def approve(data: bytes) -> ModerationResult:
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        return ModerationResult(
            moderation_disposition["value"],
            (
                "approved"
                if moderation_disposition["value"] is ModerationDisposition.APPROVED
                else "low_confidence"
            ),
            99,
            (
                99
                if moderation_disposition["value"] is ModerationDisposition.APPROVED
                else 50
            ),
        )

    service = CustomColorService(
        storage=storage,
        repository=repository,
        normalize=lambda data, mime: normalize_reference(
            data,
            declared_mime=mime,
            scanner=CleanScanner(),
            policy=policy,
            isolated=False,
        ),
        moderate=approve,
        moderation_model="vision-model",
    )
    actor = {"id": 1001}
    app = create_app(
        settings=settings(test_database_url, tmp_path / "private-colors"),
        session_factory=sessions,
        clock=lambda: NOW,
        custom_color_service=service,
        custom_color_storage=storage,
        custom_color_repository=repository,
    )
    app.dependency_overrides[require_mini_app_session] = lambda: CurrentMiniAppSession(
        telegram_user_id=actor["id"],
        expires_at=NOW + timedelta(minutes=15),
    )
    async with sessions() as session:
        session.add(
            ActiveSource(
                telegram_user_id=2002,
                chat_id=2002,
                source_message_id=77,
                telegram_file_id="telegram-file-canary",
                telegram_file_unique_id="telegram-unique-canary",
                media_kind="photo",
                mime_type="image/jpeg",
                byte_size=700,
                width=1200,
                height=800,
                accepted_at=NOW,
                updated_at=NOW,
            )
        )
        await session.commit()

    original = source_png()
    vehicle = b"ephemeral-vehicle-bytes"
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        created = await client.post(
            "/api/v1/custom-colors",
            headers={"Idempotency-Key": "e2e-upload-1"},
            files={
                "name": (None, "Bronze Satin"),
                "image": ("sample.png", original, "image/png"),
            },
        )
        assert created.status_code == 202
        color_id = UUID(created.json()["id"])
        selection_id = custom_selection_id(color_id, 1)

        actor["id"] = 2002
        catalog = await client.get("/api/v1/custom-colors")
        assert catalog.status_code == 200
        assert [item["selection_id"] for item in catalog.json()["items"]] == [
            selection_id
        ]
        assert "1001" not in catalog.text
        assert "object_key" not in catalog.text

        selected = await client.post(
            "/api/v1/palette-selection/validate",
            json={
                "color_id": selection_id,
                "client_submission_uuid": str(uuid4()),
            },
        )
        assert selected.status_code == 200
        assert "1001" not in selected.text

    async with sessions() as session:
        version = await session.scalar(
            select(CustomColorVersion).where(
                CustomColorVersion.custom_color_id == color_id
            )
        )
        assert version is not None
        canonical = storage.read(version.object_key, version.sha256)
        assert b"private-canary" not in canonical
        assert b"trailing-original-canary" not in canonical
        assert canonical != original
        intent = custom_intent(version)
        await repository.retain(session, version_id=version.id)
        await session.commit()

    provider_payload = build_generation_payload(
        model="x-ai/grok-imagine-image-quality",
        intent=intent,
        vehicle_bytes=vehicle,
        vehicle_media_type="image/jpeg",
        color_reference_bytes=canonical,
    )
    assert len(provider_payload["input_references"]) == 2
    assert "Bronze Satin" not in provider_payload["prompt"]
    assert intent.object_key not in json.dumps(provider_payload)
    assert original not in list(
        path.read_bytes() for path in (tmp_path / "private-colors").rglob("*.png")
    )
    assert vehicle not in list(
        path.read_bytes() for path in (tmp_path / "private-colors").rglob("*.png")
    )

    actor["id"] = 1001
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        deleted = await client.delete(f"/api/v1/custom-colors/{color_id}")
        assert deleted.status_code == 204
        assert storage.read(intent.object_key, intent.sha256) == canonical

        actor["id"] = 2002
        stale = await client.post(
            "/api/v1/palette-selection/validate",
            json={
                "color_id": selection_id,
                "client_submission_uuid": str(uuid4()),
            },
        )
        hidden_preview = await client.get(
            f"/api/v1/custom-colors/{color_id}/versions/1/preview"
        )
        assert stale.status_code == 409
        assert hidden_preview.status_code == 404

    async with sessions() as session:
        remaining = await service.release(session, version_id=intent.version_id)
    assert remaining == 0
    with pytest.raises(FileNotFoundError):
        storage.read(intent.object_key, intent.sha256)

    actor["id"] = 1001
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        created_unretained = await client.post(
            "/api/v1/custom-colors",
            headers={"Idempotency-Key": "e2e-upload-2"},
            files={
                "name": (None, "Copper Satin"),
                "image": ("sample.png", original, "image/png"),
            },
        )
        assert created_unretained.status_code == 202
        unretained_id = UUID(created_unretained.json()["id"])
        async with sessions() as session:
            unretained = await session.scalar(
                select(CustomColorVersion).where(
                    CustomColorVersion.custom_color_id == unretained_id
                )
            )
            assert unretained is not None
            unretained_key = unretained.object_key
            unretained_digest = unretained.sha256
        assert storage.read(unretained_key, unretained_digest)
        removed = await client.delete(f"/api/v1/custom-colors/{unretained_id}")
        assert removed.status_code == 204
    with pytest.raises(FileNotFoundError):
        storage.read(unretained_key, unretained_digest)

    moderation_disposition["value"] = ModerationDisposition.NEEDS_REVIEW
    actor["id"] = 1001
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        needs_review = await client.post(
            "/api/v1/custom-colors",
            headers={"Idempotency-Key": "e2e-upload-3"},
            files={
                "name": (None, "Copper Sample"),
                "image": ("sample.png", original, "image/png"),
            },
        )
        assert needs_review.status_code == 202
        review_id = UUID(needs_review.json()["id"])

        actor["id"] = 2002
        concealed = await client.get(
            f"/api/v1/custom-colors/{review_id}/versions/1/preview"
        )
        assert concealed.status_code == 404

        actor["id"] = 9009
        queue = await client.get("/api/v1/custom-colors/admin/review")
        assert queue.status_code == 200
        review_item = next(
            item for item in queue.json()["items"] if item["id"] == str(review_id)
        )
        assert review_item["preview_concealed"] is True
        revealed = await client.get(review_item["preview_url"])
        assert revealed.status_code == 200
        assert revealed.headers["cache-control"] == "private, no-store"
