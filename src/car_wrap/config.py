"""Offline-safe configuration for the evaluation package."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
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
DEFAULT_ADMIN_TELEGRAM_USER_IDS = (715709681,)
_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_BOT_USERNAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,27}[Bb][Oo][Tt]$")
_COOKIE_NAME_PATTERN = re.compile(r"^(?:__Host-)?[A-Za-z0-9_-]{1,64}$")
_CHANNEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
_REVISION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SUPPORTED_DOCUMENT_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_CUSTOM_COLOR_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
    }
)


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
    redis_url: SecretStr = SecretStr("redis://redis:6379/0")
    job_wakeup_channel: str = "car-wrap.jobs"
    job_relay_batch_size: int = Field(default=50, strict=True, gt=0, le=1000)
    job_relay_poll_seconds: float = Field(
        default=2.0,
        strict=True,
        gt=0,
        le=60,
    )
    job_max_active_per_user: int = Field(default=1, strict=True, gt=0, le=100)
    job_max_accepted_per_window: int = Field(
        default=10,
        strict=True,
        gt=0,
        le=10_000,
    )
    job_limit_window_seconds: int = Field(
        default=3600,
        strict=True,
        gt=0,
        le=86_400,
    )
    job_worker_poll_seconds: float = Field(default=2.0, strict=True, gt=0, le=60)
    job_lease_seconds: int = Field(default=300, strict=True, gt=0, le=3600)
    job_heartbeat_seconds: int = Field(default=30, strict=True, gt=0, le=300)
    openrouter_image_model: str = DEFAULT_OPENROUTER_IMAGE_MODEL
    generation_prompt_revision: str = "vehicle-wrap-v1"
    provider_max_output_bytes: int = Field(
        default=20 * 1024 * 1024,
        strict=True,
        gt=0,
    )
    provider_max_image_side_px: int = Field(default=8192, strict=True, gt=0)
    provider_max_image_pixels: int = Field(
        default=25_000_000,
        strict=True,
        gt=0,
    )
    telegram_result_max_bytes: int = Field(
        default=9 * 1024 * 1024,
        strict=True,
        gt=0,
        le=10 * 1024 * 1024,
    )
    telegram_result_max_side_sum: int = Field(
        default=10_000,
        strict=True,
        gt=0,
        le=10_000,
    )
    openrouter_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    openrouter_read_timeout_seconds: float = Field(default=180.0, gt=0)
    openrouter_write_timeout_seconds: float = Field(default=30.0, gt=0)
    openrouter_pool_timeout_seconds: float = Field(default=10.0, gt=0)
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
    custom_color_storage_root: Path = Path("/var/lib/car-wrap/custom-colors")
    custom_color_max_bytes: int = Field(
        default=8 * 1024 * 1024,
        strict=True,
        gt=0,
    )
    custom_color_max_side_px: int = Field(default=8192, strict=True, gt=0)
    custom_color_max_pixels: int = Field(default=20_000_000, strict=True, gt=0)
    custom_color_max_frames: int = Field(default=1, strict=True, gt=0)
    custom_color_output_long_edge_px: int = Field(
        default=2048,
        strict=True,
        gt=0,
    )
    custom_color_decode_timeout_seconds: int = Field(
        default=15,
        strict=True,
        gt=0,
    )
    custom_color_quota: int = Field(default=20, strict=True, gt=0, le=100)
    custom_color_mime_allowlist: tuple[str, ...] = (
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
    )
    clamav_socket_path: Path = Path("/run/clamav/clamd.ctl")
    moderation_vision_model: str = "google/gemini-2.5-flash"
    admin_telegram_user_ids: tuple[int, ...] = DEFAULT_ADMIN_TELEGRAM_USER_IDS
    daily_stats_hour_utc: int = Field(default=9, strict=True, ge=0, le=23)
    tbank_terminal_key: str | None = None
    tbank_password: SecretStr | None = None
    tbank_api_base_url: str = "https://securepay.tinkoff.ru/v2"
    tbank_notification_url: str | None = None
    tbank_success_url: str | None = None
    tbank_fail_url: str | None = None
    payments_production_enabled: bool = Field(default=False, strict=True)
    payments_owner_approved: bool = Field(default=False, strict=True)
    payments_phase1_report_path: Path = Path("eval/reports/phase-01.json")
    ultima_manager_contact_url: str | None = None
    subscription_scan_seconds: float = Field(default=300.0, gt=0, le=3600)

    @property
    def bot_chat_url(self) -> str:
        """A chat deep link that asks the bot to post a fresh app launcher."""

        return f"https://t.me/{self.bot_username}?start=open_app"

    @property
    def billing_chat_url(self) -> str:
        """A chat deep link that opens the authenticated bot paywall."""

        return f"https://t.me/{self.bot_username}?start=billing"

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

    @field_validator("ultima_manager_contact_url")
    @classmethod
    def validate_ultima_manager_contact_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"t.me", "telegram.me"}
            or not parsed.path.strip("/")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("manager contact must be a clean HTTPS Telegram link")
        return value.rstrip("/")

    @field_validator(
        "tbank_api_base_url",
        "tbank_notification_url",
        "tbank_success_url",
        "tbank_fail_url",
    )
    @classmethod
    def validate_tbank_urls(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("T-Bank URL must be a clean HTTPS URL")
        return value.rstrip("/")

    @field_validator("payments_phase1_report_path")
    @classmethod
    def validate_phase1_report_path(cls, value: Path) -> Path:
        if value != Path("eval/reports/phase-01.json"):
            raise ValueError("Phase 1 payment report path is fixed")
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if (
            parsed.scheme not in {"redis", "rediss"}
            or parsed.hostname is None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Redis URL must use redis or rediss")
        return value

    @field_validator("job_wakeup_channel")
    @classmethod
    def validate_job_wakeup_channel(cls, value: str) -> str:
        if not _CHANNEL_PATTERN.fullmatch(value):
            raise ValueError("invalid job wake-up channel")
        return value

    @field_validator("generation_prompt_revision")
    @classmethod
    def validate_generation_prompt_revision(cls, value: str) -> str:
        if not _REVISION_PATTERN.fullmatch(value):
            raise ValueError("invalid generation prompt revision")
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

    @field_validator("custom_color_mime_allowlist")
    @classmethod
    def validate_custom_color_mime_allowlist(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if (
            not value
            or len(value) != len(set(value))
            or any(item not in _CUSTOM_COLOR_MIME_TYPES for item in value)
        ):
            raise ValueError("unsupported custom color MIME allowlist")
        return value

    @field_validator("custom_color_storage_root", "clamav_socket_path")
    @classmethod
    def validate_absolute_private_path(cls, value: Path) -> Path:
        if not value.is_absolute() or value == Path("/"):
            raise ValueError("private media paths must be absolute and narrow")
        return value

    @field_validator("moderation_vision_model", "openrouter_image_model")
    @classmethod
    def validate_moderation_model(cls, value: str) -> str:
        if not _MODEL_NAME_PATTERN.fullmatch(value):
            raise ValueError("invalid moderation model name")
        return value

    @field_validator("admin_telegram_user_ids")
    @classmethod
    def validate_admin_ids(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if len(value) != len(set(value)) or any(item <= 0 for item in value):
            raise ValueError("administrator IDs must be unique positive integers")
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
        if self.custom_color_max_frames != 1:
            raise ValueError("custom color references must be single-frame")
        if self.custom_color_output_long_edge_px > self.custom_color_max_side_px:
            raise ValueError("output edge must not exceed decode side limit")
        if self.job_heartbeat_seconds * 3 >= self.job_lease_seconds:
            raise ValueError("job heartbeat must be below one third of the lease")
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
            "REDIS_URL": "redis_url",
            "JOB_WAKEUP_CHANNEL": "job_wakeup_channel",
            "OPENROUTER_IMAGE_MODEL": "openrouter_image_model",
            "GENERATION_PROMPT_REVISION": "generation_prompt_revision",
            "SESSION_COOKIE_NAME": "session_cookie_name",
            "CUSTOM_COLOR_STORAGE_ROOT": "custom_color_storage_root",
            "CLAMAV_SOCKET_PATH": "clamav_socket_path",
            "MODERATION_VISION_MODEL": "moderation_vision_model",
            "TBANK_TERMINAL_KEY": "tbank_terminal_key",
            "TBANK_PASSWORD": "tbank_password",
            "TBANK_API_BASE_URL": "tbank_api_base_url",
            "TBANK_NOTIFICATION_URL": "tbank_notification_url",
            "TBANK_SUCCESS_URL": "tbank_success_url",
            "TBANK_FAIL_URL": "tbank_fail_url",
            "PAYMENTS_PHASE1_REPORT_PATH": "payments_phase1_report_path",
            "ULTIMA_MANAGER_CONTACT_URL": "ultima_manager_contact_url",
        }
        integer_fields = {
            "INIT_DATA_MAX_BYTES": "init_data_max_bytes",
            "AUTH_MAX_AGE_SECONDS": "auth_max_age_seconds",
            "AUTH_FUTURE_SKEW_SECONDS": "auth_future_skew_seconds",
            "SESSION_TTL_SECONDS": "session_ttl_seconds",
            "JOB_RELAY_BATCH_SIZE": "job_relay_batch_size",
            "JOB_MAX_ACTIVE_PER_USER": "job_max_active_per_user",
            "JOB_MAX_ACCEPTED_PER_WINDOW": "job_max_accepted_per_window",
            "JOB_LIMIT_WINDOW_SECONDS": "job_limit_window_seconds",
            "JOB_LEASE_SECONDS": "job_lease_seconds",
            "JOB_HEARTBEAT_SECONDS": "job_heartbeat_seconds",
            "MAX_MEDIA_BYTES": "max_media_bytes",
            "MIN_SIDE_PX": "min_side_px",
            "MAX_SIDE_PX": "max_side_px",
            "MAX_PIXELS": "max_pixels",
            "CUSTOM_COLOR_MAX_BYTES": "custom_color_max_bytes",
            "CUSTOM_COLOR_MAX_SIDE_PX": "custom_color_max_side_px",
            "CUSTOM_COLOR_MAX_PIXELS": "custom_color_max_pixels",
            "CUSTOM_COLOR_MAX_FRAMES": "custom_color_max_frames",
            "CUSTOM_COLOR_OUTPUT_LONG_EDGE_PX": "custom_color_output_long_edge_px",
            "CUSTOM_COLOR_DECODE_TIMEOUT_SECONDS": (
                "custom_color_decode_timeout_seconds"
            ),
            "CUSTOM_COLOR_QUOTA": "custom_color_quota",
            "DAILY_STATS_HOUR_UTC": "daily_stats_hour_utc",
            "PROVIDER_MAX_OUTPUT_BYTES": "provider_max_output_bytes",
            "PROVIDER_MAX_IMAGE_SIDE_PX": "provider_max_image_side_px",
            "PROVIDER_MAX_IMAGE_PIXELS": "provider_max_image_pixels",
            "TELEGRAM_RESULT_MAX_BYTES": "telegram_result_max_bytes",
            "TELEGRAM_RESULT_MAX_SIDE_SUM": "telegram_result_max_side_sum",
        }
        for environment_name, field_name in string_fields.items():
            if environment_name in source:
                values[field_name] = source[environment_name]
        boolean_fields = {
            "PAYMENTS_PRODUCTION_ENABLED": "payments_production_enabled",
            "PAYMENTS_OWNER_APPROVED": "payments_owner_approved",
        }
        for environment_name, field_name in boolean_fields.items():
            if environment_name in source:
                raw = source[environment_name].strip().lower()
                values[field_name] = raw == "true" if raw in {"true", "false"} else raw
        for environment_name, field_name in integer_fields.items():
            if environment_name in source:
                values[field_name] = int(source[environment_name])
        if "JOB_RELAY_POLL_SECONDS" in source:
            values["job_relay_poll_seconds"] = float(source["JOB_RELAY_POLL_SECONDS"])
        if "SUBSCRIPTION_SCAN_SECONDS" in source:
            values["subscription_scan_seconds"] = float(
                source["SUBSCRIPTION_SCAN_SECONDS"]
            )
        float_fields = {
            "JOB_WORKER_POLL_SECONDS": "job_worker_poll_seconds",
            "OPENROUTER_CONNECT_TIMEOUT_SECONDS": (
                "openrouter_connect_timeout_seconds"
            ),
            "OPENROUTER_READ_TIMEOUT_SECONDS": "openrouter_read_timeout_seconds",
            "OPENROUTER_WRITE_TIMEOUT_SECONDS": "openrouter_write_timeout_seconds",
            "OPENROUTER_POOL_TIMEOUT_SECONDS": "openrouter_pool_timeout_seconds",
        }
        for environment_name, field_name in float_fields.items():
            if environment_name in source:
                values[field_name] = float(source[environment_name])
        if "DOCUMENT_MIME_ALLOWLIST" in source:
            values["document_mime_allowlist"] = tuple(
                item.strip()
                for item in source["DOCUMENT_MIME_ALLOWLIST"].split(",")
                if item.strip()
            )
        if "CUSTOM_COLOR_MIME_ALLOWLIST" in source:
            values["custom_color_mime_allowlist"] = tuple(
                item.strip()
                for item in source["CUSTOM_COLOR_MIME_ALLOWLIST"].split(",")
                if item.strip()
            )
        if source.get("ADMIN_TELEGRAM_USER_IDS", "").strip():
            configured_admins = tuple(
                int(item.strip())
                for item in source["ADMIN_TELEGRAM_USER_IDS"].split(",")
                if item.strip()
            )
            values["admin_telegram_user_ids"] = (
                *DEFAULT_ADMIN_TELEGRAM_USER_IDS,
                *(
                    user_id
                    for user_id in configured_admins
                    if user_id not in DEFAULT_ADMIN_TELEGRAM_USER_IDS
                ),
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
