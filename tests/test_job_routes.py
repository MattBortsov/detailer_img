"""Generation acceptance response behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException, Response

import car_wrap.api.routes.jobs as jobs_route
from car_wrap.api.schemas import SelectionValidationIn
from car_wrap.jobs.contracts import AcceptanceErrorCode, JobAcceptanceError


@pytest.mark.asyncio
async def test_allowance_response_does_not_wait_for_paywall_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paywall = AsyncMock()
    monkeypatch.setattr(jobs_route, "send_paywall", paywall)
    acceptance = AsyncMock(
        side_effect=JobAcceptanceError(AcceptanceErrorCode.ALLOWANCE_REQUIRED)
    )
    bot = object()
    session_factory = object()
    request = SimpleNamespace(
        query_params={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                job_acceptance_service=SimpleNamespace(accept=acceptance),
                telegram_bot=bot,
                session_factory=session_factory,
                settings=SimpleNamespace(
                    billing_chat_url="https://t.me/CarWrapBot?start=billing"
                ),
            )
        ),
    )
    background_tasks = BackgroundTasks()

    with pytest.raises(HTTPException) as caught:
        await jobs_route.accept_job(
            request=request,
            response=Response(),
            background_tasks=background_tasks,
            payload=SelectionValidationIn(
                color_id="black",
                client_submission_uuid=uuid4(),
            ),
            current=SimpleNamespace(telegram_user_id=123),
            session=AsyncMock(),
        )

    assert caught.value.status_code == 402
    assert caught.value.headers == {
        "X-Billing-Chat-Url": "https://t.me/CarWrapBot?start=billing"
    }
    paywall.assert_not_awaited()

    await background_tasks()

    paywall.assert_awaited_once_with(
        bot,
        chat_id=123,
        user_id=123,
        session_factory=session_factory,
    )
