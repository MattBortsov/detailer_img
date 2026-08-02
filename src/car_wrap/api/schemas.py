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
    kind: Literal["color", "custom", "surprise"]


class PaletteStateOut(StrictApiModel):
    palette_version: str
    choices: tuple[PaletteChoiceOut, ...]
    source_ready: bool
    source_message_id: int | None
    source_preview_url: str | None
    bot_chat_url: str
    privacy_text: str
    session_expires_at: datetime
    is_admin: bool


class PhotoReplacementOut(StrictApiModel):
    status: Literal["prompt_sent"]
    bot_chat_url: str


class CustomColorPromptOut(StrictApiModel):
    status: Literal["prompt_sent"]
    bot_chat_url: str


class SelectionValidationIn(StrictApiModel):
    color_id: str
    client_submission_uuid: UUID


class SelectionValidationOut(StrictApiModel):
    status: Literal["validated"]
    palette_version: str
    choice: PaletteChoiceOut


class JobAcceptedOut(StrictApiModel):
    job_id: UUID
    status: Literal["queued"]
    accepted: Literal[True]
    bot_chat_url: str


class CustomColorMutationIn(StrictApiModel):
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=40,
        pattern=r"^[^\x00-\x1f\x7f]*$",
    )
    reason: str | None = Field(
        default=None,
        max_length=200,
        pattern=r"^[^\x00-\x1f\x7f]*$",
    )
