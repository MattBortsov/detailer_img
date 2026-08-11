"""Owner-bound palette readiness and side-effect-free intent validation."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.api.dependencies import (
    CurrentMiniAppSession,
    database_session,
    require_mini_app_session,
)
from car_wrap.api.schemas import (
    PaletteChoiceOut,
    PaletteStateOut,
    PhotoReplacementOut,
    SelectionValidationIn,
    SelectionValidationOut,
)
from car_wrap.bot.media import MediaRejection, read_snapshotted_media
from car_wrap.bot.router import REPLACE_PHOTO_COPY, replace_photo_keyboard
from car_wrap.config import AppSettings
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


async def build_palette_state(
    session: AsyncSession,
    *,
    settings: AppSettings,
    telegram_user_id: int,
    session_expires_at: datetime,
) -> PaletteStateOut:
    """Build the safe owner-bound palette payload for an authenticated launch."""

    source = await owner_source(session, telegram_user_id)
    catalog: tuple[PaletteChoice | SurpriseChoice, ...] = (
        *PALETTE_CHOICES,
        SURPRISE_CHOICE,
    )
    return PaletteStateOut(
        palette_version=PALETTE_VERSION,
        choices=tuple(public_choice(choice) for choice in catalog),
        source_ready=source is not None,
        source_message_id=(source.source_message_id if source is not None else None),
        source_preview_url=(
            "/api/v1/active-source/image" if source is not None else None
        ),
        bot_chat_url=settings.bot_chat_url,
        privacy_text=PRIVACY_TEXT,
        session_expires_at=session_expires_at,
        is_admin=telegram_user_id in settings.admin_telegram_user_ids,
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
    settings = request.app.state.settings
    response.headers["Cache-Control"] = "no-store"
    return await build_palette_state(
        session,
        settings=settings,
        telegram_user_id=current.telegram_user_id,
        session_expires_at=current.expires_at,
    )


@router.get("/active-source/image")
async def active_source_image(
    request: Request,
    response: Response,
    current: CurrentSession,
    session: DatabaseSession,
) -> Response:
    if request.query_params:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )
    source = await owner_source(session, current.telegram_user_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active source is unavailable",
        )
    bot = request.app.state.telegram_bot
    if bot is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unavailable",
        )
    try:
        downloaded = await read_snapshotted_media(
            bot,
            file_id=source.telegram_file_id,
            declared_mime_type=source.mime_type,
            expected_byte_size=source.byte_size,
            expected_width=source.width,
            expected_height=source.height,
            settings=request.app.state.settings,
        )
    except MediaRejection:
        # Telegram file_ids belong to the bot that received them. If a bot is
        # replaced, its old file_ids cannot be downloaded by the new bot and
        # must no longer make the Mini App claim that a photo is ready.
        await session.execute(
            delete(ActiveSource).where(
                ActiveSource.telegram_user_id == current.telegram_user_id,
                ActiveSource.telegram_file_id == source.telegram_file_id,
            )
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Send the photo to the bot again",
        ) from None
    response = Response(
        content=downloaded.data,
        media_type=downloaded.mime_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )
    return response


@router.post(
    "/active-source/replacement",
    response_model=PhotoReplacementOut,
)
async def request_photo_replacement(
    request: Request,
    response: Response,
    current: CurrentSession,
    session: DatabaseSession,
) -> PhotoReplacementOut:
    if request.query_params:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )
    source = await owner_source(session, current.telegram_user_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active source is unavailable",
        )
    bot = request.app.state.telegram_bot
    if bot is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unavailable",
        )
    try:
        await bot.send_message(
            source.chat_id,
            REPLACE_PHOTO_COPY,
            reply_markup=replace_photo_keyboard(),
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not open replacement flow",
        ) from None
    response.headers["Cache-Control"] = "no-store"
    settings = request.app.state.settings
    return PhotoReplacementOut(
        status="prompt_sent",
        bot_chat_url=settings.bot_chat_url,
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
