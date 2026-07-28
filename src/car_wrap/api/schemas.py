"""Strict public response and request contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PaletteChoiceOut(StrictApiModel):
    color_id: str
    name: str
    display_hex: str | None = Field(
        default=None,
        pattern=r"^#[0-9A-F]{6}$",
    )
    kind: Literal["color", "surprise"]


class PaletteStateOut(StrictApiModel):
    palette_version: str
    choices: tuple[PaletteChoiceOut, ...]
    source_ready: bool
    source_message_id: int | None
    bot_chat_url: str
    privacy_text: str
    session_expires_at: datetime


class SelectionValidationIn(StrictApiModel):
    color_id: str
    client_submission_uuid: UUID


class SelectionValidationOut(StrictApiModel):
    status: Literal["validated"]
    palette_version: str
    choice: PaletteChoiceOut
