"""Offline-safe configuration for the evaluation package."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

DEFAULT_OPENROUTER_IMAGE_MODEL = "x-ai/grok-imagine-image-quality"
_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_BOT_USERNAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,27}[Bb][Oo][Tt]$")
_COOKIE_NAME_PATTERN = re.compile(r"^(?:__Host-)?[A-Za-z0-9_-]{1,64}$")
_SUPPORTED_DOCUMENT_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


class AppSettings(BaseModel):
    """Strict production settings for the bot and Mini App boundary."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    database_url: SecretStr
    bot_token: SecretStr
    bot_username: str
    mini_app_url: str
    session_cookie_name: str = "car_wrap_session"
    init_data_max_bytes: int = Field(default=8192, strict=True, gt=0)
    auth_max_age_seconds: int = Field(default=600, strict=True, gt=0)
    auth_future_skew_seconds: int = Field(default=30, strict=True, ge=0)
    session_ttl_seconds: int = Field(default=900, strict=True, gt=0)
    document_mime_allowlist: tuple[str, ...] = (
        "image/jpeg",
        "image/png",
        "image/webp",
    )
    max_media_bytes: int = Field(
        default=20 * 1024 * 1024,
        strict=True,
        gt=0,
    )
    min_side_px: int = Field(default=256, strict=True, gt=0)
    max_side_px: int = Field(default=4096, strict=True, gt=0)
    max_pixels: int = Field(default=16_000_000, strict=True, gt=0)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if (
            parsed.scheme != "postgresql+psycopg"
            or parsed.hostname is None
            or not parsed.path.lstrip("/")
        ):
            raise ValueError("database URL must use PostgreSQL with Psycopg")
        return value

    @field_validator("bot_username")
    @classmethod
    def validate_bot_username(cls, value: str) -> str:
        if not _BOT_USERNAME_PATTERN.fullmatch(value):
            raise ValueError("invalid Telegram bot username")
        return value

    @field_validator("mini_app_url")
    @classmethod
    def validate_mini_app_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Mini App URL must be an HTTPS URL")
        return value

    @field_validator("session_cookie_name")
    @classmethod
    def validate_cookie_name(cls, value: str) -> str:
        if not _COOKIE_NAME_PATTERN.fullmatch(value):
            raise ValueError("invalid session cookie name")
        return value

    @field_validator("document_mime_allowlist")
    @classmethod
    def validate_document_mime_allowlist(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if (
            not value
            or len(value) != len(set(value))
            or any(item not in _SUPPORTED_DOCUMENT_MIME_TYPES for item in value)
        ):
            raise ValueError("unsupported document MIME allowlist")
        return value

    @model_validator(mode="after")
    def validate_related_limits(self) -> Self:
        if self.auth_future_skew_seconds >= self.auth_max_age_seconds:
            raise ValueError("auth future skew must be below maximum age")
        if self.session_ttl_seconds <= self.auth_future_skew_seconds:
            raise ValueError("session TTL must exceed auth future skew")
        if self.min_side_px > self.max_side_px:
            raise ValueError("minimum side must not exceed maximum side")
        if self.max_pixels < self.min_side_px**2:
            raise ValueError("pixel limit contradicts minimum dimensions")
        return self

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> Self:
        """Load only the documented application variables."""

        source = os.environ if environ is None else environ
        values: dict[str, object] = {
            "database_url": source.get("DATABASE_URL"),
            "bot_token": source.get("TELEGRAM_BOT_TOKEN"),
            "bot_username": source.get("TELEGRAM_BOT_USERNAME"),
            "mini_app_url": source.get("MINI_APP_URL"),
        }
        string_fields = {
            "SESSION_COOKIE_NAME": "session_cookie_name",
        }
        integer_fields = {
            "INIT_DATA_MAX_BYTES": "init_data_max_bytes",
            "AUTH_MAX_AGE_SECONDS": "auth_max_age_seconds",
            "AUTH_FUTURE_SKEW_SECONDS": "auth_future_skew_seconds",
            "SESSION_TTL_SECONDS": "session_ttl_seconds",
            "MAX_MEDIA_BYTES": "max_media_bytes",
            "MIN_SIDE_PX": "min_side_px",
            "MAX_SIDE_PX": "max_side_px",
            "MAX_PIXELS": "max_pixels",
        }
        for environment_name, field_name in string_fields.items():
            if environment_name in source:
                values[field_name] = source[environment_name]
        for environment_name, field_name in integer_fields.items():
            if environment_name in source:
                values[field_name] = int(source[environment_name])
        if "DOCUMENT_MIME_ALLOWLIST" in source:
            values["document_mime_allowlist"] = tuple(
                item.strip()
                for item in source["DOCUMENT_MIME_ALLOWLIST"].split(",")
                if item.strip()
            )
        return cls.model_validate(values)


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
