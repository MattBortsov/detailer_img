"""Offline-safe configuration for the evaluation package."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_OPENROUTER_IMAGE_MODEL = "x-ai/grok-imagine-image-quality"
_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


class EvalSettings(BaseModel):
    """Non-secret settings that are safe to construct for offline commands.

    Provider credentials deliberately do not belong to this model. The live
    provider boundary owns reading ``OPENROUTER_API_KEY`` immediately before a
    paid request.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    openrouter_image_model: str = DEFAULT_OPENROUTER_IMAGE_MODEL
    fixture_max_bytes: int = Field(default=20 * 1024 * 1024, strict=True, gt=0)
    provider_max_output_bytes: int = Field(
        default=20 * 1024 * 1024,
        strict=True,
        gt=0,
    )
    provider_max_image_width: int = Field(default=8192, strict=True, gt=0)
    provider_max_image_height: int = Field(default=8192, strict=True, gt=0)
    provider_max_image_pixels: int = Field(
        default=25_000_000,
        strict=True,
        gt=0,
    )
    openrouter_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    openrouter_read_timeout_seconds: float = Field(default=180.0, gt=0)
    openrouter_write_timeout_seconds: float = Field(default=30.0, gt=0)
    openrouter_pool_timeout_seconds: float = Field(default=10.0, gt=0)

    @field_validator("openrouter_image_model")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        if not _MODEL_NAME_PATTERN.fullmatch(value):
            raise ValueError("invalid provider model name")
        return value

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> Self:
        """Load only non-secret settings from an environment mapping."""

        source = os.environ if environ is None else environ
        model = source.get(
            "OPENROUTER_IMAGE_MODEL",
            DEFAULT_OPENROUTER_IMAGE_MODEL,
        )
        return cls(openrouter_image_model=model)
