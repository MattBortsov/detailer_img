"""Application configuration contracts."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from car_wrap.config import AppSettings, EvalSettings


def valid_environment() -> dict[str, str]:
    return {
        "DATABASE_URL": ("postgresql+psycopg://car_wrap:database-canary@db/car_wrap"),
        "TELEGRAM_BOT_TOKEN": "bot-token-canary",
        "TELEGRAM_BOT_USERNAME": "CarWrapBot",
        "MINI_APP_URL": "https://wrap.example.com/app",
        "SESSION_COOKIE_NAME": "__Host-car_wrap_session",
        "INIT_DATA_MAX_BYTES": "8192",
        "AUTH_MAX_AGE_SECONDS": "600",
        "AUTH_FUTURE_SKEW_SECONDS": "30",
        "SESSION_TTL_SECONDS": "900",
        "DOCUMENT_MIME_ALLOWLIST": "image/jpeg,image/png,image/webp",
        "MAX_MEDIA_BYTES": "20971520",
        "MIN_SIDE_PX": "256",
        "MAX_SIDE_PX": "4096",
        "MAX_PIXELS": "16000000",
        "CUSTOM_COLOR_STORAGE_ROOT": "/var/lib/car-wrap/custom-colors",
        "CUSTOM_COLOR_MAX_BYTES": "8388608",
        "CUSTOM_COLOR_MAX_SIDE_PX": "8192",
        "CUSTOM_COLOR_MAX_PIXELS": "20000000",
        "CUSTOM_COLOR_MAX_FRAMES": "1",
        "CUSTOM_COLOR_OUTPUT_LONG_EDGE_PX": "2048",
        "CUSTOM_COLOR_DECODE_TIMEOUT_SECONDS": "15",
        "CUSTOM_COLOR_QUOTA": "20",
        "CUSTOM_COLOR_MIME_ALLOWLIST": (
            "image/jpeg,image/png,image/webp,image/heic,image/heif"
        ),
        "CLAMAV_SOCKET_PATH": "/run/clamav/clamd.ctl",
        "MODERATION_VISION_MODEL": "google/gemini-2.5-flash",
        "ADMIN_TELEGRAM_USER_IDS": "101,202",
        "UNRELATED_SECRET": "must-not-be-read",
    }


def test_app_settings_load_only_explicit_environment_contract() -> None:
    settings = AppSettings.from_environment(valid_environment())

    assert settings.bot_username == "CarWrapBot"
    assert settings.mini_app_url == "https://wrap.example.com/app"
    assert settings.document_mime_allowlist == (
        "image/jpeg",
        "image/png",
        "image/webp",
    )
    assert settings.max_media_bytes == 20 * 1024 * 1024
    assert settings.custom_color_max_bytes == 8 * 1024 * 1024
    assert settings.custom_color_quota == 20
    assert settings.admin_telegram_user_ids == (101, 202)
    assert settings.model_config["frozen"] is True
    assert settings.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError):
        AppSettings(**settings.model_dump(), unexpected=True)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DATABASE_URL", "sqlite:///local.db"),
        ("DATABASE_URL", "postgresql://user:pass@db/app"),
        ("MINI_APP_URL", "http://wrap.example.com/app"),
        ("TELEGRAM_BOT_USERNAME", "@bad-name"),
        ("INIT_DATA_MAX_BYTES", "0"),
        ("AUTH_MAX_AGE_SECONDS", "0"),
        ("AUTH_FUTURE_SKEW_SECONDS", "600"),
        ("SESSION_TTL_SECONDS", "0"),
        ("MAX_MEDIA_BYTES", "-1"),
        ("MIN_SIDE_PX", "4097"),
        ("MAX_SIDE_PX", "255"),
        ("MAX_PIXELS", "65535"),
        ("CUSTOM_COLOR_STORAGE_ROOT", "/"),
        ("CUSTOM_COLOR_MAX_BYTES", "0"),
        ("CUSTOM_COLOR_MAX_FRAMES", "2"),
        ("CUSTOM_COLOR_QUOTA", "0"),
        ("CLAMAV_SOCKET_PATH", "relative/clamd.sock"),
        ("ADMIN_TELEGRAM_USER_IDS", "101,101"),
    ],
)
def test_app_settings_reject_unsafe_values(key: str, value: str) -> None:
    environ = valid_environment()
    environ[key] = value

    with pytest.raises(ValidationError):
        AppSettings.from_environment(environ)


def test_app_settings_redacts_credentials_in_all_common_outputs() -> None:
    environ = valid_environment()
    settings = AppSettings.from_environment(environ)
    outputs = (
        repr(settings),
        str(settings),
        json.dumps(settings.model_dump(mode="json")),
    )

    for output in outputs:
        assert "bot-token-canary" not in output
        assert "database-canary" not in output
        assert "must-not-be-read" not in output


def test_app_settings_validation_error_does_not_expose_credentials() -> None:
    environ = valid_environment()
    environ["DATABASE_URL"] = "mysql://car_wrap:database-canary@db/car_wrap"

    with pytest.raises(ValidationError) as caught:
        AppSettings.from_environment(environ)

    rendered = str(caught.value)
    assert "database-canary" not in rendered
    assert "bot-token-canary" not in rendered


def test_eval_settings_contract_is_unchanged() -> None:
    assert EvalSettings.from_environment({}).fixture_max_bytes == 20 * 1024 * 1024
