"""Authenticated custom color creation, catalog and moderation API."""

from __future__ import annotations

import asyncio
import base64
import re
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.api.custom_color_upload import parse_custom_color_upload
from car_wrap.api.dependencies import (
    CurrentMiniAppSession,
    database_session,
    require_mini_app_session,
)
from car_wrap.api.schemas import CustomColorMutationIn
from car_wrap.custom_colors.media import MediaValidationError
from car_wrap.custom_colors.repository import (
    ColorStatus,
    InvalidTransitionError,
    QuotaExceededError,
)
from car_wrap.db.models import CustomColor, CustomColorVersion
from car_wrap.palette import custom_selection_id

router = APIRouter(prefix="/api/v1/custom-colors", tags=["custom-colors"])
_CURSOR = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
CurrentSession = Annotated[
    CurrentMiniAppSession,
    Depends(require_mini_app_session),
]
DatabaseSession = Annotated[AsyncSession, Depends(database_session)]


def _admin(request: Request, user_id: int) -> bool:
    return user_id in request.app.state.settings.admin_telegram_user_ids


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Custom color not found")


def _encode_cursor(approved_at: datetime, color_id: UUID) -> str:
    value = f"{approved_at.isoformat()}|{color_id}".encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, UUID]:
    if not _CURSOR.fullmatch(value):
        raise HTTPException(status_code=400, detail="Invalid cursor")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(value + padding).decode("ascii")
        timestamp, raw_id = raw.split("|", 1)
        approved_at = datetime.fromisoformat(timestamp)
        color_id = UUID(raw_id)
    except (ValueError, UnicodeError):
        raise HTTPException(status_code=400, detail="Invalid cursor") from None
    if approved_at.tzinfo is None:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return approved_at, color_id


def _public_item(color: CustomColor, version: CustomColorVersion) -> dict[str, Any]:
    return {
        "selection_id": custom_selection_id(color.id, version.version),
        "name": color.display_name,
        "version": version.version,
        "preview_url": (
            f"/api/v1/custom-colors/{color.id}/versions/{version.version}/preview"
        ),
        "color_structure": version.color_structure or "unspecified",
        "finish": version.finish or "unspecified",
        "approved_at": color.approved_at,
    }


@router.post("", status_code=202)
async def create_custom_color(
    request: Request,
    current: CurrentSession,
    session: DatabaseSession,
) -> JSONResponse:
    service = getattr(request.app.state, "custom_color_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Service unavailable")
    key = request.headers.get("idempotency-key")
    if key is None:
        raise HTTPException(status_code=400, detail="Idempotency key is required")
    upload = await parse_custom_color_upload(request)
    try:
        color = await service.create(
            session,
            owner_id=current.telegram_user_id,
            display_name=upload.name,
            upload=upload.image,
            declared_mime=upload.mime_type,
            idempotency_key=key,
            color_structure=upload.color_structure,
            finish=upload.finish,
        )
    except QuotaExceededError:
        raise HTTPException(
            status_code=409,
            detail="Custom color quota reached",
        ) from None
    except (MediaValidationError, ValueError):
        raise HTTPException(
            status_code=422,
            detail="Image or color name is invalid",
        ) from None
    return JSONResponse(
        status_code=202,
        content={
            "id": str(color.id),
            "name": color.display_name,
            "status": color.status,
            "version": color.current_version,
        },
    )


@router.get("")
async def public_catalog(
    request: Request,
    current: CurrentSession,
    session: DatabaseSession,
) -> dict[str, Any]:
    del current
    if set(request.query_params) - {"cursor", "limit", "structure", "finish"}:
        raise HTTPException(status_code=400, detail="Invalid request")
    try:
        limit = int(request.query_params.get("limit", "20"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid request") from None
    if not 1 <= limit <= 50:
        raise HTTPException(status_code=400, detail="Invalid request")
    conditions: list[Any] = [CustomColor.status == ColorStatus.APPROVED.value]
    structure = request.query_params.get("structure")
    finish = request.query_params.get("finish")
    if structure is not None:
        if structure not in {"solid", "multicolor"}:
            raise HTTPException(status_code=400, detail="Invalid request")
        conditions.append(CustomColorVersion.color_structure == structure)
    if finish is not None:
        if finish not in {"matte", "satin", "gloss"}:
            raise HTTPException(status_code=400, detail="Invalid request")
        conditions.append(CustomColorVersion.finish == finish)
    raw_cursor = request.query_params.get("cursor")
    if raw_cursor:
        approved_at, color_id = _decode_cursor(raw_cursor)
        conditions.append(
            or_(
                CustomColor.approved_at < approved_at,
                and_(
                    CustomColor.approved_at == approved_at,
                    CustomColor.id < color_id,
                ),
            )
        )
    statement = (
        select(CustomColor, CustomColorVersion)
        .join(
            CustomColorVersion,
            and_(
                CustomColorVersion.custom_color_id == CustomColor.id,
                CustomColorVersion.version == CustomColor.current_version,
            ),
        )
        .where(*conditions)
        .order_by(CustomColor.approved_at.desc(), CustomColor.id.desc())
        .limit(limit + 1)
    )
    rows = list((await session.execute(statement)).all())
    visible = rows[:limit]
    next_cursor = None
    if len(rows) > limit and visible:
        last_color = visible[-1][0]
        if last_color.approved_at is not None:
            next_cursor = _encode_cursor(last_color.approved_at, last_color.id)
    return {
        "items": [_public_item(color, version) for color, version in visible],
        "next_cursor": next_cursor,
    }


@router.get("/mine")
async def owner_catalog(
    current: CurrentSession,
    session: DatabaseSession,
) -> dict[str, Any]:
    rows = list(
        await session.execute(
            select(CustomColor, CustomColorVersion)
            .join(
                CustomColorVersion,
                and_(
                    CustomColorVersion.custom_color_id == CustomColor.id,
                    CustomColorVersion.version == CustomColor.current_version,
                ),
            )
            .where(
                CustomColor.telegram_user_id == current.telegram_user_id,
                CustomColor.status != ColorStatus.DELETED.value,
            )
            .order_by(CustomColor.created_at.desc(), CustomColor.id.desc())
        )
    )
    return {
        "items": [
            {
                "id": str(color.id),
                "name": color.display_name,
                "status": color.status,
                "version": color.current_version,
                "color_structure": version.color_structure or "unspecified",
                "finish": version.finish or "unspecified",
            }
            for color, version in rows
        ]
    }


@router.get("/{color_id}/versions/{version}/preview")
async def preview(
    request: Request,
    color_id: UUID,
    version: int,
    current: CurrentSession,
    session: DatabaseSession,
) -> Response:
    row = (
        await session.execute(
            select(CustomColor, CustomColorVersion)
            .join(CustomColorVersion)
            .where(
                CustomColor.id == color_id,
                CustomColorVersion.version == version,
            )
        )
    ).one_or_none()
    if row is None:
        raise _not_found()
    color, stored = row
    allowed = color.status == ColorStatus.APPROVED.value or (
        color.telegram_user_id == current.telegram_user_id
        and color.status in {ColorStatus.PENDING.value, ColorStatus.NEEDS_REVIEW.value}
    )
    if (
        not allowed
        and _admin(request, current.telegram_user_id)
        and request.query_params.get("reveal") == "true"
        and set(request.query_params) == {"reveal"}
        and color.status in {ColorStatus.PENDING.value, ColorStatus.NEEDS_REVIEW.value}
    ):
        allowed = True
    if not allowed:
        raise _not_found()
    storage = getattr(request.app.state, "custom_color_storage", None)
    if storage is None:
        raise HTTPException(status_code=503, detail="Service unavailable")
    data = await asyncio.to_thread(storage.read, stored.object_key, stored.sha256)
    return Response(
        content=data,
        media_type="image/png",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.patch("/{color_id}")
async def rename_owner(
    request: Request,
    color_id: UUID,
    current: CurrentSession,
    session: DatabaseSession,
    payload: CustomColorMutationIn | None = None,
) -> dict[str, str]:
    if payload is None or payload.name is None:
        raise HTTPException(status_code=422, detail="Invalid request")
    repository = getattr(request.app.state, "custom_color_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="Service unavailable")
    try:
        color = await repository.rename(
            session,
            color_id=color_id,
            display_name=payload.name,
            owner_id=current.telegram_user_id,
        )
    except LookupError:
        raise _not_found() from None
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid request") from None
    await session.commit()
    return {"id": str(color.id), "name": color.display_name}


@router.delete("/{color_id}", status_code=204)
async def delete_owner(
    request: Request,
    color_id: UUID,
    current: CurrentSession,
    session: DatabaseSession,
) -> Response:
    service = getattr(request.app.state, "custom_color_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Service unavailable")
    try:
        await service.delete(
            session,
            color_id=color_id,
            owner_id=current.telegram_user_id,
        )
    except (LookupError, InvalidTransitionError):
        raise _not_found() from None
    return Response(status_code=204)


@router.get("/admin/review")
async def admin_review(
    request: Request,
    current: CurrentSession,
    session: DatabaseSession,
) -> dict[str, Any]:
    if not _admin(request, current.telegram_user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    colors = (
        await session.execute(
            select(CustomColor)
            .where(
                CustomColor.status.in_(
                    [ColorStatus.PENDING.value, ColorStatus.NEEDS_REVIEW.value]
                )
            )
            .order_by(CustomColor.created_at.desc(), CustomColor.id.desc())
            .limit(100)
        )
    ).scalars()
    return {
        "items": [
            {
                "id": str(color.id),
                "name": color.display_name,
                "status": color.status,
                "preview_concealed": True,
                "preview_url": (
                    f"/api/v1/custom-colors/{color.id}/versions/"
                    f"{color.current_version}/preview?reveal=true"
                ),
            }
            for color in colors
        ]
    }


@router.post("/admin/{color_id}/{action}")
async def admin_action(
    request: Request,
    color_id: UUID,
    action: str,
    current: CurrentSession,
    session: DatabaseSession,
    payload: CustomColorMutationIn | None = None,
) -> dict[str, str]:
    if not _admin(request, current.telegram_user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    mutation = payload or CustomColorMutationIn()
    if action == "rename":
        if mutation.name is None:
            raise HTTPException(status_code=422, detail="Invalid request")
        repository = getattr(request.app.state, "custom_color_repository", None)
        if repository is None:
            raise HTTPException(status_code=503, detail="Service unavailable")
        try:
            color = await repository.rename(
                session,
                color_id=color_id,
                display_name=mutation.name,
                admin_actor_id=current.telegram_user_id,
                admin_reason=mutation.reason,
            )
        except LookupError:
            raise _not_found() from None
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid request") from None
        await session.commit()
        return {"id": str(color.id), "status": color.status}
    mapping = {
        "approve": ColorStatus.APPROVED,
        "reject": ColorStatus.REJECTED,
        "hide": ColorStatus.HIDDEN,
        "restore": ColorStatus.APPROVED,
        "delete": ColorStatus.DELETED,
    }
    if action not in mapping:
        raise _not_found()
    if action == "delete":
        service = getattr(request.app.state, "custom_color_service", None)
        if service is None:
            raise HTTPException(status_code=503, detail="Service unavailable")
        try:
            color = await service.delete(
                session,
                color_id=color_id,
                admin_actor_id=current.telegram_user_id,
                admin_reason=mutation.reason,
            )
        except (LookupError, InvalidTransitionError):
            raise _not_found() from None
        return {"id": str(color.id), "status": color.status}
    repository = getattr(request.app.state, "custom_color_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="Service unavailable")
    try:
        color = await repository.transition(
            session,
            color_id=color_id,
            target=mapping[action],
            reason_code=f"admin_{action}",
            admin_actor_id=current.telegram_user_id,
            admin_action=action,
            admin_reason=mutation.reason,
        )
    except (LookupError, InvalidTransitionError):
        raise _not_found() from None
    await session.commit()
    return {"id": str(color.id), "status": color.status}
