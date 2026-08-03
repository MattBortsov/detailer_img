"""Deterministic Telegram Mini App initData authentication."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
from pydantic import SecretStr

import car_wrap.services.telegram_auth as telegram_auth
from car_wrap.config import AppSettings
from car_wrap.db.models import MiniAppSession
from car_wrap.services.telegram_auth import (
    AuthenticatedTelegramUser,
    TelegramAuthenticationError,
    exchange_init_data,
    validate_init_data,
)

BOT_TOKEN = "123456:bot-token-canary"  # noqa: S105
NOW = datetime(2026, 7, 28, 9, 30, tzinfo=UTC)


def settings(**overrides: object) -> AppSettings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://user:pass@db/test",
        "bot_token": BOT_TOKEN,
        "bot_username": "CarWrapBot",
        "mini_app_url": "https://wrap.example.com/app",
        "init_data_max_bytes": 4096,
        "auth_max_age_seconds": 600,
        "auth_future_skew_seconds": 30,
    }
    values.update(overrides)
    return AppSettings.model_validate(values)


def signed_init_data(
    *,
    auth_date: datetime = NOW,
    user: object | None = None,
    query_id: str = "query-canary",
    extra: dict[str, str] | None = None,
) -> str:
    fields = {
        "auth_date": str(int(auth_date.timestamp())),
        "query_id": query_id,
        "user": json.dumps(
            {"id": 1001, "first_name": "User"},
            separators=(",", ":"),
        )
        if user is None
        else json.dumps(user, separators=(",", ":")),
        **(extra or {}),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256,
    ).digest()
    fields["hash"] = hmac.new(
        secret,
        check.encode(),
        hashlib.sha256,
    ).hexdigest()
    return "&".join(
        f"{quote(key, safe='')}={quote(value, safe='')}"
        for key, value in fields.items()
    )


def test_valid_init_data_returns_only_owner_and_auth_time() -> None:
    authenticated = validate_init_data(
        signed_init_data(),
        settings=settings(),
        now=NOW,
    )

    assert authenticated.telegram_user_id == 1001
    assert authenticated.auth_date == NOW
    assert set(authenticated.__slots__) == {"telegram_user_id", "auth_date"}
    assert "query-canary" not in repr(authenticated)
    assert "first_name" not in repr(authenticated)


class ExchangeSession:
    def __init__(self, row: MiniAppSession | None) -> None:
        self.row = row
        self.added: list[MiniAppSession] = []
        self.flushes = 0
        self.rollbacks = 0

    async def scalar(self, statement: object) -> MiniAppSession | None:
        del statement
        return self.row

    def add(self, row: MiniAppSession) -> None:
        self.added.append(row)
        self.row = row

    async def flush(self) -> None:
        self.flushes += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_reuses_valid_init_data_by_rotating_opaque_session_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_init_data = signed_init_data()
    existing = MiniAppSession(
        token_sha256="a" * 64,
        init_data_sha256=hashlib.sha256(raw_init_data.encode()).hexdigest(),
        telegram_user_id=1001,
        auth_date=NOW,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        revoked_at=None,
    )
    session = ExchangeSession(existing)
    monkeypatch.setattr(
        telegram_auth,
        "validate_init_data",
        lambda *args, **kwargs: AuthenticatedTelegramUser(1001, NOW),
    )

    issued = await exchange_init_data(
        session,  # type: ignore[arg-type]
        raw_init_data,
        settings=settings(session_ttl_seconds=900),
        now=NOW,
    )

    assert session.added == []
    assert session.flushes == 1
    assert session.rollbacks == 0
    assert (
        existing.token_sha256
        == hashlib.sha256(issued.token.get_secret_value().encode()).hexdigest()
    )
    assert existing.expires_at == NOW + timedelta(minutes=15)
    assert issued.token != SecretStr("a" * 43)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "hash=x&auth_date=1&user=%ZZ&query_id=q",
        "hash=x&auth_date=1&user=%00&query_id=q",
        signed_init_data() + "&hash=" + "a" * 64,
        signed_init_data() + "&auth_date=1",
        signed_init_data().replace("query_id=", "query_id"),
    ],
)
def test_rejects_malformed_controls_encoding_and_duplicates(raw: str) -> None:
    with pytest.raises(TelegramAuthenticationError) as caught:
        validate_init_data(raw, settings=settings(), now=NOW)

    assert str(caught.value) == "Telegram authentication failed"
    assert caught.value.__cause__ is None


def test_rejects_input_over_utf8_byte_limit() -> None:
    raw = signed_init_data(extra={"start_param": "я" * 100})

    with pytest.raises(TelegramAuthenticationError):
        validate_init_data(
            raw,
            settings=settings(init_data_max_bytes=50),
            now=NOW,
        )


@pytest.mark.parametrize(
    "auth_date",
    [
        NOW - timedelta(seconds=601),
        NOW + timedelta(seconds=31),
    ],
)
def test_rejects_stale_and_excessively_future_auth_date(
    auth_date: datetime,
) -> None:
    with pytest.raises(TelegramAuthenticationError):
        validate_init_data(
            signed_init_data(auth_date=auth_date),
            settings=settings(),
            now=NOW,
        )


def test_accepts_exact_age_and_future_skew_boundaries() -> None:
    for auth_date in (
        NOW - timedelta(seconds=600),
        NOW + timedelta(seconds=30),
    ):
        assert (
            validate_init_data(
                signed_init_data(auth_date=auth_date),
                settings=settings(),
                now=NOW,
            ).telegram_user_id
            == 1001
        )


@pytest.mark.parametrize(
    ("user", "extra"),
    [
        ({"id": 0}, None),
        ({"id": "1001"}, None),
        (None, {"chat_type": "group"}),
        (
            None,
            {"chat": '{"id":2002,"type":"private"}'},
        ),
        (
            None,
            {"chat": '{"id":-100,"type":"group"}'},
        ),
    ],
)
def test_rejects_invalid_user_and_non_private_context(
    user: object | None,
    extra: dict[str, str] | None,
) -> None:
    with pytest.raises(TelegramAuthenticationError):
        validate_init_data(
            signed_init_data(user=user, extra=extra),
            settings=settings(),
            now=NOW,
        )


def test_tampered_user_is_rejected_without_leaking_or_chaining() -> None:
    raw = signed_init_data().replace(
        quote('"id":1001', safe=""),
        quote('"id":9999', safe=""),
    )

    with pytest.raises(TelegramAuthenticationError) as caught:
        validate_init_data(raw, settings=settings(), now=NOW)

    rendered = repr(caught.value) + str(caught.value)
    assert "9999" not in rendered
    assert "bot-token-canary" not in rendered
    assert "query-canary" not in rendered
    assert caught.value.__cause__ is None
