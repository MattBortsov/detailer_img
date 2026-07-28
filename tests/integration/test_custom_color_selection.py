"""Approved cross-user custom color selection contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from car_wrap.api.app import create_app
from car_wrap.api.dependencies import (
    CurrentMiniAppSession,
    require_mini_app_session,
)
from car_wrap.config import AppSettings
from car_wrap.db.models import ActiveSource, CustomColor, CustomColorVersion
from car_wrap.palette import custom_selection_id

pytestmark = [pytest.mark.postgresql, pytest.mark.asyncio]
NOW = datetime(2026, 7, 28, 13, tzinfo=UTC)


def settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "database_url": "postgresql+psycopg://user:pass@db/test",
            "bot_token": "token",
            "bot_username": "CarWrapBot",
            "mini_app_url": "https://wrap.example.com/app",
        }
    )


async def test_approved_cross_user_selection_and_hidden_rejection(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    color_id = uuid4()
    version_id = uuid4()
    async with sessions() as session:
        session.add(
            ActiveSource(
                telegram_user_id=1001,
                chat_id=1001,
                source_message_id=77,
                telegram_file_id="file",
                telegram_file_unique_id="unique",
                media_kind="photo",
                mime_type="image/jpeg",
                byte_size=100,
                width=1000,
                height=700,
                accepted_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            CustomColor(
                id=color_id,
                telegram_user_id=2002,
                display_name="Bronze",
                status="approved",
                current_version=1,
                approved_at=NOW,
            )
        )
        session.add(
            CustomColorVersion(
                id=version_id,
                custom_color_id=color_id,
                version=1,
                object_key="aa/bb/" + "c" * 32 + ".png",
                sha256="d" * 64,
                byte_size=100,
                width=80,
                height=60,
            )
        )
        await session.commit()

    app = create_app(settings=settings(), session_factory=sessions, clock=lambda: NOW)
    app.dependency_overrides[require_mini_app_session] = lambda: CurrentMiniAppSession(
        telegram_user_id=1001,
        expires_at=NOW + timedelta(minutes=15),
    )
    selection = custom_selection_id(color_id, 1)
    payload = {
        "color_id": selection,
        "client_submission_uuid": str(uuid4()),
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        accepted = await client.post(
            "/api/v1/palette-selection/validate",
            json=payload,
        )
        rejected_responses = []
        for invalid_status in ("pending", "rejected", "hidden", "deleted"):
            async with sessions() as session:
                color = await session.get(CustomColor, color_id)
                assert color is not None
                color.status = invalid_status
                if invalid_status in {"pending", "rejected"}:
                    color.approved_at = None
                await session.commit()
            rejected_responses.append(
                await client.post(
                    "/api/v1/palette-selection/validate",
                    json=payload,
                )
            )
        async with sessions() as session:
            color = await session.get(CustomColor, color_id)
            assert color is not None
            color.status = "approved"
            color.current_version = 2
            await session.commit()
        rejected_responses.extend(
            [
                await client.post(
                    "/api/v1/palette-selection/validate",
                    json=payload,
                ),
                await client.post(
                    "/api/v1/palette-selection/validate",
                    json={
                        **payload,
                        "color_id": custom_selection_id(uuid4(), 1),
                    },
                ),
                await client.post(
                    "/api/v1/palette-selection/validate",
                    json={**payload, "color_id": "custom:forged:v1"},
                ),
            ]
        )

    assert accepted.status_code == 200
    assert accepted.json()["choice"] == {
        "color_id": selection,
        "name": "Bronze",
        "display_hex": None,
        "kind": "custom",
    }
    assert "2002" not in accepted.text
    assert len(rejected_responses) == 7
    assert all(response.status_code == 409 for response in rejected_responses)
    assert all(
        response.json() == {"detail": "Palette selection is invalid"}
        for response in rejected_responses
    )
    assert all("2002" not in response.text for response in rejected_responses)
