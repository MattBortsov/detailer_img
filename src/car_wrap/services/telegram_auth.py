"""Strict Telegram Mini App launch authentication."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from car_wrap.config import AppSettings
from car_wrap.db.models import MiniAppSession

_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_FIELDS = frozenset({"hash", "auth_date", "user", "query_id"})


class TelegramAuthenticationError(ValueError):
    """Uniform public failure for every rejected launch proof."""

    def __init__(self) -> None:
        super().__init__("Telegram authentication failed")


@dataclass(frozen=True, slots=True)
class AuthenticatedTelegramUser:
    """Safe identity extracted only after signature verification."""

    telegram_user_id: int
    auth_date: datetime


@dataclass(frozen=True, slots=True)
class IssuedMiniAppSession:
    """Opaque browser capability with a redacted raw token."""

    token: SecretStr
    telegram_user_id: int
    expires_at: datetime


def _contains_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _parse_unique_fields(raw_init_data: str) -> dict[str, str]:
    if not raw_init_data or _contains_control(raw_init_data):
        raise TelegramAuthenticationError
    if _INVALID_PERCENT_ESCAPE.search(raw_init_data):
        raise TelegramAuthenticationError
    pairs = parse_qsl(
        raw_init_data,
        keep_blank_values=True,
        strict_parsing=True,
        encoding="utf-8",
        errors="strict",
        separator="&",
    )
    if not pairs or any(
        not key or _contains_control(key) or _contains_control(value)
        for key, value in pairs
    ):
        raise TelegramAuthenticationError
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise TelegramAuthenticationError
    fields = dict(pairs)
    if not _REQUIRED_FIELDS.issubset(fields):
        raise TelegramAuthenticationError
    return fields


def _verify_signature(
    fields: dict[str, str],
    *,
    bot_token: str,
) -> None:
    received_hash = fields["hash"]
    if not _SHA256_HEX.fullmatch(received_hash):
        raise TelegramAuthenticationError
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(fields.items()) if key != "hash"
    )
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(received_hash, expected_hash):
        raise TelegramAuthenticationError


def _validated_user_id(fields: dict[str, str]) -> int:
    user = json.loads(fields["user"])
    if not isinstance(user, dict):
        raise TelegramAuthenticationError
    user_id = user.get("id")
    if type(user_id) is not int or user_id <= 0:
        raise TelegramAuthenticationError
    return user_id


def _validate_private_context(
    fields: dict[str, str],
    *,
    user_id: int,
) -> None:
    chat_type = fields.get("chat_type")
    if chat_type is not None and chat_type != "private":
        raise TelegramAuthenticationError
    raw_chat = fields.get("chat")
    if raw_chat is None:
        return
    chat = json.loads(raw_chat)
    if (
        not isinstance(chat, dict)
        or chat.get("type") != "private"
        or type(chat.get("id")) is not int
        or chat["id"] != user_id
    ):
        raise TelegramAuthenticationError


def validate_init_data(
    raw_init_data: str,
    *,
    settings: AppSettings,
    now: datetime,
) -> AuthenticatedTelegramUser:
    """Authenticate structure, HMAC, freshness, and private launch context."""

    try:
        if (
            not isinstance(raw_init_data, str)
            or len(raw_init_data.encode("utf-8")) > settings.init_data_max_bytes
            or now.tzinfo is None
        ):
            raise TelegramAuthenticationError
        fields = _parse_unique_fields(raw_init_data)
        _verify_signature(
            fields,
            bot_token=settings.bot_token.get_secret_value(),
        )
        if not fields["query_id"]:
            raise TelegramAuthenticationError
        auth_date_raw = fields["auth_date"]
        if not auth_date_raw.isascii() or not auth_date_raw.isdigit():
            raise TelegramAuthenticationError
        auth_date = datetime.fromtimestamp(int(auth_date_raw), tz=UTC)
        normalized_now = now.astimezone(UTC)
        age_seconds = (normalized_now - auth_date).total_seconds()
        if (
            age_seconds > settings.auth_max_age_seconds
            or age_seconds < -settings.auth_future_skew_seconds
        ):
            raise TelegramAuthenticationError
        user_id = _validated_user_id(fields)
        _validate_private_context(fields, user_id=user_id)
        return AuthenticatedTelegramUser(
            telegram_user_id=user_id,
            auth_date=auth_date,
        )
    except TelegramAuthenticationError:
        raise
    except (OverflowError, TypeError, UnicodeError, ValueError):
        raise TelegramAuthenticationError from None


async def exchange_init_data(
    session: AsyncSession,
    raw_init_data: str,
    *,
    settings: AppSettings,
    now: datetime,
) -> IssuedMiniAppSession:
    """Exchange valid launch proof for a digest-backed opaque token."""

    authenticated = validate_init_data(
        raw_init_data,
        settings=settings,
        now=now,
    )
    init_data_sha256 = hashlib.sha256(raw_init_data.encode("utf-8")).hexdigest()
    raw_token = secrets.token_urlsafe(32)
    token_sha256 = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = now.astimezone(UTC) + timedelta(seconds=settings.session_ttl_seconds)
    row = await session.scalar(
        select(MiniAppSession)
        .where(MiniAppSession.init_data_sha256 == init_data_sha256)
        .with_for_update()
    )
    if row is None:
        row = MiniAppSession(
            token_sha256=token_sha256,
            init_data_sha256=init_data_sha256,
            telegram_user_id=authenticated.telegram_user_id,
            auth_date=authenticated.auth_date,
            created_at=now.astimezone(UTC),
            expires_at=expires_at,
            revoked_at=None,
        )
        session.add(row)
    else:
        # Telegram may reuse valid initData when a menu-button WebView is
        # reopened. Its signature and freshness were verified above, so rotate
        # this browser capability instead of rejecting the legitimate launch.
        row.token_sha256 = token_sha256
        row.telegram_user_id = authenticated.telegram_user_id
        row.auth_date = authenticated.auth_date
        row.expires_at = expires_at
        row.revoked_at = None
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # Two identical launches can race before either transaction creates the
        # digest row. Reloading it after the unique constraint wins is safe: the
        # launch proof was authenticated before this point.
        row = await session.scalar(
            select(MiniAppSession)
            .where(MiniAppSession.init_data_sha256 == init_data_sha256)
            .with_for_update()
        )
        if row is None:
            raise TelegramAuthenticationError from None
        row.token_sha256 = token_sha256
        row.telegram_user_id = authenticated.telegram_user_id
        row.auth_date = authenticated.auth_date
        row.expires_at = expires_at
        row.revoked_at = None
        try:
            await session.flush()
        except IntegrityError:
            raise TelegramAuthenticationError from None
    return IssuedMiniAppSession(
        token=SecretStr(raw_token),
        telegram_user_id=authenticated.telegram_user_id,
        expires_at=expires_at,
    )
