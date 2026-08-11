"""Truthful in-memory Telegram result and recovery delivery."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from enum import StrEnum
from typing import Any, Protocol

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyParameters,
    WebAppInfo,
)

from car_wrap.generation.result import TelegramPhoto
from car_wrap.jobs.contracts import DeliveryReceipt, ExecutionErrorCode

GENERATION_STARTED_COPY = "🎨 Генерация запущена. Результат придёт в этот чат."
MENU_CALLBACK_DATA = "main_menu"
PAYMENT_CONFIRMED_COPY = (
    "✅ Оплата прошла! Баланс генераций пополнен.\n\n"
    "Нажмите «Сгенерировать», чтобы выбрать цвет и запустить генерацию."
)
_DISCLAIMER = (
    "Это AI-визуализация. Реальный цвет может отличаться "
    "в зависимости от вашего экрана."
)
_MAX_RETRY_AFTER_SECONDS = 30

RECOVERY_COPY: dict[ExecutionErrorCode, str] = {
    ExecutionErrorCode.SOURCE_UNAVAILABLE: (
        "Не удалось получить исходное фото. Отправьте фото ещё раз и создайте "
        "новый запрос."
    ),
    ExecutionErrorCode.SOURCE_CHANGED: (
        "Исходное фото изменилось или недоступно. Отправьте его ещё раз и "
        "создайте новый запрос."
    ),
    ExecutionErrorCode.CUSTOM_REFERENCE_UNAVAILABLE: (
        "Этот пользовательский цвет сейчас недоступен. Выберите другой цвет "
        "и создайте новый запрос."
    ),
    ExecutionErrorCode.PROVIDER_UNAVAILABLE: (
        "Сервис визуализации временно недоступен. Попробуйте создать новый "
        "запрос позже."
    ),
    ExecutionErrorCode.PROVIDER_REJECTED: (
        "Не удалось создать визуализацию для этого фото. Попробуйте другое "
        "фото или цвет."
    ),
    ExecutionErrorCode.PROVIDER_INVALID_RESPONSE: (
        "Не удалось получить корректную визуализацию. Создайте новый запрос позже."
    ),
    ExecutionErrorCode.PROVIDER_AMBIGUOUS: (
        "Не удалось подтвердить результат генерации. Мы не будем повторять её "
        "автоматически. При необходимости создайте новый запрос."
    ),
    ExecutionErrorCode.RESULT_INVALID: (
        "Полученную визуализацию не удалось безопасно отправить. Создайте новый "
        "запрос позже."
    ),
    ExecutionErrorCode.DELIVERY_UNAVAILABLE: (
        "Не удалось отправить визуализацию в чат. Создайте новый запрос позже."
    ),
    ExecutionErrorCode.DELIVERY_AMBIGUOUS: (
        "Не удалось подтвердить доставку визуализации. Генерация не будет "
        "запущена повторно автоматически."
    ),
    ExecutionErrorCode.INTERNAL_FAILURE: (
        "Не удалось завершить запрос. Попробуйте создать новый запрос позже."
    ),
}


class DeliveryFailureKind(StrEnum):
    UNAVAILABLE = "unavailable"
    AMBIGUOUS = "ambiguous"


class DeliveryFailure(RuntimeError):
    def __init__(self, kind: DeliveryFailureKind) -> None:
        self.kind = kind
        super().__init__(kind.value)


class TelegramSender(Protocol):
    async def send_photo(self, **kwargs: Any) -> Any: ...

    async def send_message(self, **kwargs: Any) -> Any: ...

    async def send_chat_action(self, **kwargs: Any) -> Any: ...


def result_caption(bot_username: str) -> str:
    return (
        "✅ Ваше фото готово!\n\n"
        f"Результат работы @{bot_username}\n\n"
        f"<i>{_DISCLAIMER}</i>"
    )


def result_keyboard(mini_app_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✨ Новая генерация",
                    web_app=WebAppInfo(url=mini_app_url),
                ),
                InlineKeyboardButton(
                    text="☰ Меню",
                    callback_data=MENU_CALLBACK_DATA,
                ),
            ]
        ]
    )


def payment_confirmed_keyboard(mini_app_url: str) -> InlineKeyboardMarkup:
    """Offer the next useful action after a confirmed purchase."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎨 Сгенерировать",
                    web_app=WebAppInfo(url=mini_app_url),
                ),
                InlineKeyboardButton(
                    text="☰ Меню",
                    callback_data=MENU_CALLBACK_DATA,
                ),
            ]
        ]
    )


async def send_generation_started(
    sender: TelegramSender,
    *,
    chat_id: int,
    source_message_id: int,
) -> None:
    """Best-effort acknowledgement; generation remains authoritative in PostgreSQL."""

    with suppress(Exception):
        await sender.send_message(
            chat_id=chat_id,
            text=GENERATION_STARTED_COPY,
            reply_parameters=ReplyParameters(
                message_id=source_message_id,
                allow_sending_without_reply=True,
            ),
        )
    with suppress(Exception):
        await sender.send_chat_action(chat_id=chat_id, action="upload_photo")


async def send_result(
    sender: TelegramSender,
    photo: TelegramPhoto,
    *,
    chat_id: int,
    source_message_id: int,
    bot_username: str,
    mini_app_url: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> DeliveryReceipt:
    if chat_id <= 0 or source_message_id <= 0:
        raise ValueError("invalid Telegram result target")
    kwargs = {
        "chat_id": chat_id,
        "photo": BufferedInputFile(photo.data, filename="result.jpg"),
        "caption": result_caption(bot_username),
        "parse_mode": ParseMode.HTML,
        "reply_markup": result_keyboard(mini_app_url),
        "reply_parameters": ReplyParameters(
            message_id=source_message_id,
            allow_sending_without_reply=False,
        ),
    }
    for attempt_number in range(2):
        try:
            message = await sender.send_photo(**kwargs)
            message_id = getattr(message, "message_id", None)
            result_chat = getattr(getattr(message, "chat", None), "id", None)
            if type(message_id) is not int or message_id <= 0 or result_chat != chat_id:
                raise DeliveryFailure(DeliveryFailureKind.AMBIGUOUS)
            return DeliveryReceipt(chat_id=chat_id, message_id=message_id)
        except TelegramRetryAfter as error:
            if (
                attempt_number != 0
                or error.retry_after <= 0
                or error.retry_after > _MAX_RETRY_AFTER_SECONDS
            ):
                raise DeliveryFailure(DeliveryFailureKind.UNAVAILABLE) from None
            await sleep(float(error.retry_after))
        except TelegramNetworkError:
            raise DeliveryFailure(DeliveryFailureKind.AMBIGUOUS) from None
        except DeliveryFailure:
            raise
        except Exception:
            raise DeliveryFailure(DeliveryFailureKind.UNAVAILABLE) from None
    raise AssertionError("unreachable delivery retry state")


async def send_recovery(
    sender: TelegramSender,
    *,
    chat_id: int,
    source_message_id: int,
    code: ExecutionErrorCode,
) -> None:
    try:
        await sender.send_message(
            chat_id=chat_id,
            text=RECOVERY_COPY[code],
            reply_parameters=ReplyParameters(
                message_id=source_message_id,
                allow_sending_without_reply=True,
            ),
        )
    except Exception:
        return
