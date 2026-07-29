"""Telegram result caption, receipt, and retry contracts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendPhoto

from car_wrap.bot.delivery import (
    DeliveryFailure,
    DeliveryFailureKind,
    result_caption,
    send_result,
)
from car_wrap.generation.result import TelegramPhoto
from car_wrap.jobs.contracts import IntentKind


def _photo() -> TelegramPhoto:
    return TelegramPhoto(
        data=b"\xff\xd8\xff-result-canary",
        width=100,
        height=80,
        byte_count=17,
        image_format="jpeg",
        sha256="a" * 64,
    )


class Sender:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    async def send_photo(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def send_message(self, **kwargs: Any) -> Any:
        return None


def test_captions_are_truthful_and_server_owned() -> None:
    assert result_caption(IntentKind.PALETTE, "Чёрный сатиновый") == (
        "Цвет: Чёрный сатиновый.\n\n"
        "Это AI-визуализация. Реальный цвет плёнки или краски может отличаться."
    )
    assert result_caption(IntentKind.SURPRISE, "ignored") == (
        "Удиви меня.\n\n"
        "Это AI-визуализация. Реальный цвет плёнки или краски может отличаться."
    )


@pytest.mark.asyncio
async def test_sends_reply_and_requires_matching_receipt() -> None:
    sender = Sender([SimpleNamespace(message_id=33, chat=SimpleNamespace(id=10))])
    receipt = await send_result(
        sender,
        _photo(),
        chat_id=10,
        source_message_id=20,
        intent_kind=IntentKind.PALETTE,
        display_name="Графит",
    )
    assert receipt.message_id == 33
    call = sender.calls[0]
    assert call["reply_parameters"].message_id == 20
    assert call["reply_parameters"].allow_sending_without_reply is False
    assert call["photo"].data == _photo().data


@pytest.mark.asyncio
async def test_retry_after_reuses_identical_bytes_once() -> None:
    method = SendPhoto(chat_id=10, photo="file-id")
    sender = Sender(
        [
            TelegramRetryAfter(method, "rate limited", retry_after=1),
            SimpleNamespace(message_id=34, chat=SimpleNamespace(id=10)),
        ]
    )
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    await send_result(
        sender,
        _photo(),
        chat_id=10,
        source_message_id=20,
        intent_kind=IntentKind.SURPRISE,
        display_name="Удиви меня",
        sleep=sleep,
    )
    assert sleeps == [1.0]
    assert len(sender.calls) == 2
    assert sender.calls[0]["photo"].data == sender.calls[1]["photo"].data


@pytest.mark.asyncio
async def test_network_failure_is_ambiguous_without_retry() -> None:
    method = SendPhoto(chat_id=10, photo="file-id")
    sender = Sender([TelegramNetworkError(method, "token-secret")])
    with pytest.raises(DeliveryFailure) as caught:
        await send_result(
            sender,
            _photo(),
            chat_id=10,
            source_message_id=20,
            intent_kind=IntentKind.PALETTE,
            display_name="Графит",
        )
    assert caught.value.kind is DeliveryFailureKind.AMBIGUOUS
    assert len(sender.calls) == 1
    assert "token-secret" not in repr(caught.value)
