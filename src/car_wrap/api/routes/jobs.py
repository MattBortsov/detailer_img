"""Authenticated durable generation-job acceptance."""

from __future__ import annotations

from typing import Annotated

from aiogram import Bot
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from car_wrap.api.dependencies import (
    CurrentMiniAppSession,
    database_session,
    require_mini_app_session,
)
from car_wrap.api.schemas import JobAcceptedOut, SelectionValidationIn
from car_wrap.bot.router import send_paywall
from car_wrap.jobs.contracts import AcceptanceErrorCode, JobAcceptanceError

router = APIRouter(prefix="/api/v1", tags=["jobs"])
CurrentSession = Annotated[
    CurrentMiniAppSession,
    Depends(require_mini_app_session),
]
DatabaseSession = Annotated[AsyncSession, Depends(database_session)]

_ERRORS: dict[AcceptanceErrorCode, tuple[int, str]] = {
    AcceptanceErrorCode.NO_SOURCE: (
        status.HTTP_409_CONFLICT,
        "Сначала отправьте фото автомобиля в чат с ботом.",  # noqa: RUF001
    ),
    AcceptanceErrorCode.INVALID_SELECTION: (
        status.HTTP_409_CONFLICT,
        "Выбранный цвет больше недоступен. Выберите другой.",
    ),
    AcceptanceErrorCode.ACTIVE_LIMIT: (
        status.HTTP_429_TOO_MANY_REQUESTS,
        "Дождитесь результата текущего запроса и попробуйте снова.",
    ),
    AcceptanceErrorCode.RECENT_LIMIT: (
        status.HTTP_429_TOO_MANY_REQUESTS,
        "Слишком много запросов за короткое время. Попробуйте позже.",
    ),
    AcceptanceErrorCode.ALLOWANCE_REQUIRED: (
        status.HTTP_402_PAYMENT_REQUIRED,
        "Для новой генерации выберите подходящий тариф.",
    ),
}


async def _send_paywall_safely(
    bot: Bot,
    *,
    chat_id: int,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    try:
        await send_paywall(
            bot,
            chat_id=chat_id,
            user_id=user_id,
            session_factory=session_factory,
        )
    except Exception:
        return


@router.post(
    "/jobs",
    response_model=JobAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def accept_job(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    payload: SelectionValidationIn,
    current: CurrentSession,
    session: DatabaseSession,
) -> JobAcceptedOut:
    if request.query_params:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )
    service = request.app.state.job_acceptance_service
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unavailable",
        )
    try:
        accepted = await service.accept(
            session,
            user_id=current.telegram_user_id,
            color_id=payload.color_id,
            submission_uuid=payload.client_submission_uuid,
        )
    except JobAcceptanceError as error:
        status_code, message = _ERRORS[error.code]
        if error.code is AcceptanceErrorCode.ALLOWANCE_REQUIRED:
            bot = request.app.state.telegram_bot
            if bot is not None:
                background_tasks.add_task(
                    _send_paywall_safely,
                    bot,
                    chat_id=current.telegram_user_id,
                    user_id=current.telegram_user_id,
                    session_factory=request.app.state.session_factory,
                )
        headers = (
            {"X-Billing-Chat-Url": request.app.state.settings.billing_chat_url}
            if error.code is AcceptanceErrorCode.ALLOWANCE_REQUIRED
            else None
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": error.code.value, "message": message},
            headers=headers,
        ) from None
    response.headers["Cache-Control"] = "no-store"
    settings = request.app.state.settings
    return JobAcceptedOut(
        job_id=accepted.job_id,
        status="queued",
        accepted=True,
        bot_chat_url=settings.bot_chat_url,
    )
