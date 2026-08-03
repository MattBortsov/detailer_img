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
        "REDIS_URL": "rediss://:redis-canary@redis.example.com:6380/0",
        "JOB_WAKEUP_CHANNEL": "car-wrap.jobs.test",
        "JOB_RELAY_BATCH_SIZE": "50",
        "JOB_RELAY_POLL_SECONDS": "2.5",
        "JOB_MAX_ACTIVE_PER_USER": "1",
        "JOB_MAX_ACCEPTED_PER_WINDOW": "10",
        "JOB_LIMIT_WINDOW_SECONDS": "3600",
        "JOB_WORKER_POLL_SECONDS": "2",
        "JOB_LEASE_SECONDS": "300",
        "JOB_HEARTBEAT_SECONDS": "30",
        "OPENROUTER_IMAGE_MODEL": "x-ai/grok-imagine-image-quality",
        "GENERATION_PROMPT_REVISION": "vehicle-wrap-v1",
        "PROVIDER_MAX_OUTPUT_BYTES": "20971520",
        "PROVIDER_MAX_IMAGE_SIDE_PX": "8192",
        "PROVIDER_MAX_IMAGE_PIXELS": "25000000",
        "TELEGRAM_RESULT_MAX_BYTES": "9437184",
        "TELEGRAM_RESULT_MAX_SIDE_SUM": "10000",
        "OPENROUTER_CONNECT_TIMEOUT_SECONDS": "10",
        "OPENROUTER_READ_TIMEOUT_SECONDS": "180",
        "OPENROUTER_WRITE_TIMEOUT_SECONDS": "30",
        "OPENROUTER_POOL_TIMEOUT_SECONDS": "10",
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
    assert settings.admin_telegram_user_ids == (715709681, 101, 202)
    assert settings.job_wakeup_channel == "car-wrap.jobs.test"
    assert settings.job_relay_batch_size == 50
    assert settings.job_relay_poll_seconds == 2.5
    assert settings.job_max_active_per_user == 1
    assert settings.job_max_accepted_per_window == 10
    assert settings.job_limit_window_seconds == 3600
    assert settings.job_worker_poll_seconds == 2
    assert settings.job_lease_seconds == 300
    assert settings.job_heartbeat_seconds == 30
    assert settings.provider_max_output_bytes == 20 * 1024 * 1024
    assert settings.provider_max_image_side_px == 8192
    assert settings.provider_max_image_pixels == 25_000_000
    assert settings.telegram_result_max_bytes == 9 * 1024 * 1024
    assert settings.telegram_result_max_side_sum == 10_000
    assert settings.openrouter_connect_timeout_seconds == 10
    assert settings.openrouter_read_timeout_seconds == 180
    assert settings.openrouter_write_timeout_seconds == 30
    assert settings.openrouter_pool_timeout_seconds == 10
    assert settings.openrouter_image_model == "x-ai/grok-imagine-image-quality"
    assert settings.generation_prompt_revision == "vehicle-wrap-v1"
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
        ("REDIS_URL", "http://redis.example.com/0"),
        ("JOB_WAKEUP_CHANNEL", "bad channel"),
        ("JOB_RELAY_BATCH_SIZE", "0"),
        ("JOB_RELAY_POLL_SECONDS", "0"),
        ("JOB_MAX_ACTIVE_PER_USER", "0"),
        ("JOB_MAX_ACCEPTED_PER_WINDOW", "0"),
        ("JOB_LIMIT_WINDOW_SECONDS", "0"),
        ("JOB_WORKER_POLL_SECONDS", "0"),
        ("JOB_LEASE_SECONDS", "90"),
        ("JOB_HEARTBEAT_SECONDS", "100"),
        ("PROVIDER_MAX_OUTPUT_BYTES", "0"),
        ("PROVIDER_MAX_IMAGE_SIDE_PX", "0"),
        ("PROVIDER_MAX_IMAGE_PIXELS", "0"),
        ("TELEGRAM_RESULT_MAX_BYTES", "10485761"),
        ("TELEGRAM_RESULT_MAX_SIDE_SUM", "10001"),
        ("OPENROUTER_CONNECT_TIMEOUT_SECONDS", "0"),
        ("OPENROUTER_READ_TIMEOUT_SECONDS", "0"),
        ("OPENROUTER_WRITE_TIMEOUT_SECONDS", "0"),
        ("OPENROUTER_POOL_TIMEOUT_SECONDS", "0"),
        ("OPENROUTER_IMAGE_MODEL", "bad model?"),
        ("GENERATION_PROMPT_REVISION", "../prompt"),
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
        assert "redis-canary" not in output
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
