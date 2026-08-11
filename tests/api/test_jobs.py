"""Strict public durable-job acceptance API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from car_wrap.api.app import create_app
from car_wrap.api.dependencies import (
    CurrentMiniAppSession,
    require_mini_app_session,
)
from car_wrap.config import AppSettings
from car_wrap.jobs.contracts import (
    AcceptanceErrorCode,
    AcceptedJob,
    JobAcceptanceError,
    JobStatus,
)

NOW = datetime(2026, 7, 29, 7, tzinfo=UTC)
JOB_ID = UUID("5e58ee4e-3064-4704-b0df-388ae9de8953")
SUBMISSION_ID = "6db32e02-9371-450c-851f-f187bea635d5"


class FakeService:
    def __init__(self, error: AcceptanceErrorCode | None = None) -> None:
        self.error = error
        self.calls: list[tuple[int, str, UUID]] = []

    async def accept(
        self,
        session: object,
        *,
        user_id: int,
        color_id: str,
        submission_uuid: UUID,
    ) -> AcceptedJob:
        del session
        self.calls.append((user_id, color_id, submission_uuid))
        if self.error is not None:
            raise JobAcceptanceError(self.error)
        return AcceptedJob(JOB_ID, JobStatus.QUEUED)


class SessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        return None


def app(service: FakeService) -> Any:
    settings = AppSettings.model_validate(
        {
            "database_url": "postgresql+psycopg://user:pass@db/test",
            "bot_token": "token",
            "bot_username": "CarWrapBot",
            "mini_app_url": "https://wrap.example.com/app",
        }
    )
    built = create_app(
        settings=settings,
        session_factory=lambda: SessionContext(),
        clock=lambda: NOW,
        job_acceptance_service=service,
    )
    built.dependency_overrides[require_mini_app_session] = lambda: (
        CurrentMiniAppSession(
            telegram_user_id=1001,
            expires_at=NOW + timedelta(minutes=15),
        )
    )
    return built


@pytest.mark.asyncio
async def test_jobs_returns_strict_202_projection() -> None:
    service = FakeService()
    async with AsyncClient(
        transport=ASGITransport(app=app(service)),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/jobs",
            json={"color_id": "charcoal", "client_submission_uuid": SUBMISSION_ID},
        )
    assert response.status_code == 202
    assert response.json() == {
        "job_id": str(JOB_ID),
        "status": "queued",
        "accepted": True,
        "bot_chat_url": "https://t.me/CarWrapBot?start=open_app",
    }
    assert service.calls == [(1001, "charcoal", UUID(SUBMISSION_ID))]


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (AcceptanceErrorCode.NO_SOURCE, 409),
        (AcceptanceErrorCode.INVALID_SELECTION, 409),
        (AcceptanceErrorCode.ACTIVE_LIMIT, 429),
        (AcceptanceErrorCode.RECENT_LIMIT, 429),
    ],
)
@pytest.mark.asyncio
async def test_jobs_maps_only_stable_sanitized_errors(
    error: AcceptanceErrorCode,
    status_code: int,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app(FakeService(error))),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/jobs",
            json={"color_id": "charcoal", "client_submission_uuid": SUBMISSION_ID},
        )
    assert response.status_code == status_code
    assert response.json()["detail"]["code"] == error.value
    for forbidden in ("token", "redis", "postgres", "file_id", "openrouter"):
        assert forbidden not in response.text.lower()


@pytest.mark.asyncio
async def test_allowance_required_routes_the_mini_app_to_the_bot_paywall() -> None:
    async with AsyncClient(
        transport=ASGITransport(
            app=app(FakeService(AcceptanceErrorCode.ALLOWANCE_REQUIRED))
        ),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/jobs",
            json={"color_id": "charcoal", "client_submission_uuid": SUBMISSION_ID},
        )

    assert response.status_code == 402
    assert response.json()["detail"]["code"] == "allowance_required"
    assert response.headers["X-Billing-Chat-Url"] == (
        "https://t.me/CarWrapBot?start=billing"
    )


@pytest.mark.asyncio
async def test_jobs_rejects_extra_fields_and_query_parameters() -> None:
    built = app(FakeService())
    async with AsyncClient(
        transport=ASGITransport(app=built),
        base_url="https://testserver",
    ) as client:
        extra = await client.post(
            "/api/v1/jobs",
            json={
                "color_id": "charcoal",
                "client_submission_uuid": SUBMISSION_ID,
                "telegram_user_id": 9999,
            },
        )
        query = await client.post(
            "/api/v1/jobs?user_id=9999",
            json={"color_id": "charcoal", "client_submission_uuid": SUBMISSION_ID},
        )
    assert extra.status_code == 422
    assert query.status_code == 400
