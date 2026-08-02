"""Shared production construction for custom-color processing."""

from __future__ import annotations

import os

import httpx

from car_wrap.config import AppSettings
from car_wrap.custom_colors.media import (
    ClamdInstreamScanner,
    MediaPolicy,
    normalize_reference,
)
from car_wrap.custom_colors.moderation import (
    ModerationResult,
    moderate_reference,
)
from car_wrap.custom_colors.repository import CustomColorRepository
from car_wrap.custom_colors.service import CustomColorService
from car_wrap.custom_colors.storage import FilesystemPrivateStorage


def build_custom_color_service(
    settings: AppSettings,
    *,
    provider_client: httpx.AsyncClient,
) -> tuple[CustomColorService, FilesystemPrivateStorage, CustomColorRepository]:
    storage = FilesystemPrivateStorage(
        settings.custom_color_storage_root,
        max_object_bytes=settings.custom_color_max_bytes,
    )
    repository = CustomColorRepository(quota=settings.custom_color_quota)
    scanner = ClamdInstreamScanner(
        settings.clamav_socket_path,
        max_bytes=settings.custom_color_max_bytes,
    )
    media_policy = MediaPolicy(
        max_bytes=settings.custom_color_max_bytes,
        max_side_px=settings.custom_color_max_side_px,
        max_pixels=settings.custom_color_max_pixels,
        max_frames=settings.custom_color_max_frames,
        output_long_edge_px=settings.custom_color_output_long_edge_px,
        decode_timeout_seconds=settings.custom_color_decode_timeout_seconds,
    )
    api_key = os.environ.get("OPENROUTER_API_KEY")

    async def moderate(data: bytes) -> ModerationResult:
        return await moderate_reference(
            data,
            client=provider_client,
            api_key=api_key,
            model=settings.moderation_vision_model,
        )

    service = CustomColorService(
        storage=storage,
        repository=repository,
        normalize=lambda data, mime: normalize_reference(
            data,
            declared_mime=mime,
            scanner=scanner,
            policy=media_policy,
        ),
        moderate=moderate,
        moderation_model=settings.moderation_vision_model,
    )
    return service, storage, repository
