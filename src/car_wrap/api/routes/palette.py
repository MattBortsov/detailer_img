"""Owner-bound palette readiness and side-effect-free intent validation."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.api.dependencies import (
    CurrentMiniAppSession,
    database_session,
    require_mini_app_session,
)
from car_wrap.api.schemas import (
    PaletteChoiceOut,
    PaletteStateOut,
    SelectionValidationIn,
    SelectionValidationOut,
)
from car_wrap.db.models import ActiveSource, CustomColor, CustomColorVersion
from car_wrap.palette import (
    PALETTE_CHOICES,
    PALETTE_VERSION,
    SURPRISE_CHOICE,
    PaletteChoice,
    PaletteLookupError,
    SurpriseChoice,
    custom_selection_id,
    get_palette_choice,
    parse_custom_selection,
)

router = APIRouter(prefix="/api/v1", tags=["palette"])

PRIVACY_TEXT = (
    "Приложение не сохраняет файлы изображений. Telegram и AI-провайдер "
    "обрабатывают фото для создания визуализации."
)
CurrentSession = Annotated[
    CurrentMiniAppSession,
    Depends(require_mini_app_session),
]
DatabaseSession = Annotated[AsyncSession, Depends(database_session)]


def public_choice(
    choice: PaletteChoice | SurpriseChoice,
) -> PaletteChoiceOut:
    if isinstance(choice, PaletteChoice):
        return PaletteChoiceOut(
            color_id=choice.color_id,
            name=choice.ui_name_ru,
            display_hex=choice.display_hex,
            kind="color",
        )
    return PaletteChoiceOut(
        color_id=choice.color_id,
        name=choice.ui_name_ru,
        display_hex=None,
        kind="surprise",
    )


async def owner_source(
    session: AsyncSession,
    telegram_user_id: int,
) -> ActiveSource | None:
    return cast(
        ActiveSource | None,
        await session.scalar(
            select(ActiveSource).where(
                ActiveSource.telegram_user_id == telegram_user_id
            )
        ),
    )


@router.get("/palette-state", response_model=PaletteStateOut)
async def palette_state(
    request: Request,
    response: Response,
    current: CurrentSession,
    session: DatabaseSession,
) -> PaletteStateOut:
    if request.query_params:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )
    source = await owner_source(session, current.telegram_user_id)
    settings = request.app.state.settings
    response.headers["Cache-Control"] = "no-store"
    catalog: tuple[PaletteChoice | SurpriseChoice, ...] = (
        *PALETTE_CHOICES,
        SURPRISE_CHOICE,
    )
    return PaletteStateOut(
        palette_version=PALETTE_VERSION,
        choices=tuple(public_choice(choice) for choice in catalog),
        source_ready=source is not None,
        source_message_id=(source.source_message_id if source is not None else None),
        bot_chat_url=f"https://t.me/{settings.bot_username}",
        privacy_text=PRIVACY_TEXT,
        session_expires_at=current.expires_at,
    )


@router.post(
    "/palette-selection/validate",
    response_model=SelectionValidationOut,
)
async def validate_selection(
    request: Request,
    response: Response,
    payload: SelectionValidationIn,
    current: CurrentSession,
    session: DatabaseSession,
) -> SelectionValidationOut:
    if request.query_params:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )
    try:
        public = public_choice(get_palette_choice(payload.color_id))
    except PaletteLookupError:
        try:
            requested = parse_custom_selection(payload.color_id)
        except PaletteLookupError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Palette selection is invalid",
            ) from None
        row = (
            await session.execute(
                select(CustomColor, CustomColorVersion)
                .join(CustomColorVersion)
                .where(
                    CustomColor.id == requested.color_id,
                    CustomColor.status == "approved",
                    CustomColor.current_version == requested.version,
                    CustomColorVersion.version == requested.version,
                )
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Palette selection is invalid",
            ) from None
        custom_color, custom_version = row
        public = PaletteChoiceOut(
            color_id=custom_selection_id(
                custom_color.id,
                custom_version.version,
            ),
            name=custom_color.display_name,
            display_hex=None,
            kind="custom",
        )
    source = await owner_source(session, current.telegram_user_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active source is unavailable",
        )
    response.headers["Cache-Control"] = "no-store"
    return SelectionValidationOut(
        status="validated",
        palette_version=PALETTE_VERSION,
        choice=public,
    )
