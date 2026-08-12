"""Private-chat Telegram intake and truthful active-source replies."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    Document,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from aiogram.types.base import TelegramObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from car_wrap.billing.catalog import get_product
from car_wrap.billing.contracts import ProductKind
from car_wrap.billing.gateway import PaymentActivationDenied
from car_wrap.billing.repository import BillingRepository
from car_wrap.bot.delivery import MENU_CALLBACK_DATA
from car_wrap.bot.media import (
    MediaRejection,
    MediaRejectionCode,
    read_supported_media,
    read_supported_media_bytes,
)
from car_wrap.config import AppSettings
from car_wrap.db.models import Subscription, UltimaLead
from car_wrap.palette import custom_selection_id
from car_wrap.services.active_source import (
    ActiveSourceDecision,
    set_active_source,
)
from car_wrap.services.telegram_users import record_telegram_user

NO_SOURCE_COPY = (
    "Отправьте фото автомобиля или мотоцикла. Лучше всего подойдёт чёткий "
    "кадр, где хорошо видны окрашенные части."
)
UNSUPPORTED_MESSAGE_COPY = (
    "Нужна фотография автомобиля или мотоцикла. Отправьте фото или изображение файлом."
)
WINNING_SOURCE_COPY = "Теперь выберите цвет."
OLDER_SOURCE_COPY = "Фото принято, но для оклейки уже выбрано более новое фото."
INFO_CALLBACK_PREFIX = "info:"
INFO_HOW_TO = "how_to"
INFO_PRICES = "prices"
INFO_SUPPORT = "support"
INFO_PRIVACY = "privacy"
OPEN_APP_COPY = "Откройте приложение кнопкой ниже."
REPLACE_PHOTO_COPY = "Пришлите новое фото"
REPLACE_PHOTO_CANCEL_CALLBACK_DATA = "replace_photo:cancel"
CUSTOM_COLOR_REQUEST_CALLBACK_DATA = "custom_color:request"
CUSTOM_COLOR_STRUCTURE_PREFIX = "custom_color:structure:"
CUSTOM_COLOR_FINISH_PREFIX = "custom_color:finish:"
CUSTOM_COLOR_GENERATE_PREFIX = "ccg:"
CUSTOM_COLOR_STRUCTURE_COPY = "Какая структура цвета у плёнки?"
CUSTOM_COLOR_FINISH_COPY = "Какая поверхность у плёнки?"
CUSTOM_COLOR_REQUEST_COPY = (
    "Пришлите фото образца плёнки следующим сообщением именно как файл "
    "(скрепка → Файл), чтобы Telegram не сжал качество."
)
CUSTOM_COLOR_PROCESSING_COPY = "Изображение обработано на {percent}%"
CUSTOM_COLOR_READY_COPY = "✅ Образец принят: {name}"
CUSTOM_COLOR_PENDING_COPY = (
    "Образец получен, но требует дополнительной проверки. "
    "Когда он будет одобрен, цвет появится в User Colors."
)
CUSTOM_COLOR_RETAKE_COPY = (
    "Не удалось надёжно определить материал. Сфотографируйте один образец "
    "крупнее при ровном свете, чтобы плёнка занимала большую часть кадра. "
    "Заводская надпись на образце допустима."
)
CUSTOM_COLOR_FAILED_COPY = "Не удалось обработать образец. Отправьте файл ещё раз."

ActiveSourceSetter = Callable[..., Awaitable[ActiveSourceDecision]]
CustomColorCreator = Any
JobAcceptor = Any
PaymentProcessor = Any

PAYWALL_CALLBACK_DATA = "billing:open"
PACKAGES_CALLBACK_DATA = "billing:packages"
MONTHLY_CALLBACK_DATA = "billing:monthly"
BILLING_BACK_CALLBACK_DATA = "billing:back"
ULTIMA_CALLBACK_DATA = "billing:ultima"
BILLING_PRODUCT_PREFIX = "billing:product:"
BILLING_CONSENT_PREFIX = "billing:consent:"
BILLING_CANCEL_CALLBACK_DATA = "billing:cancel"
BILLING_CANCEL_INTRO_RECURRING_CALLBACK_DATA = "billing:cancel_intro_recurring"
BILLING_INTRO_SAVED_PREFIX = "billing:intro_saved:"
ULTIMA_COPY = (
    "Свяжитесь с менеджером, чтобы узнать стоимость конкретно для вашего бизнеса."
)
PAYMENTS_UNAVAILABLE_COPY = "Оплата сейчас недоступна. Попробуйте позже."
logger = logging.getLogger(__name__)


class UserTrackingMiddleware(BaseMiddleware):
    """Persist only an authenticated private user's Telegram ID and activity."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id: int | None = None
        if isinstance(event, Message) and _trusted_private_message(event):
            user_id = event.from_user.id if event.from_user is not None else None
        elif isinstance(event, CallbackQuery):
            message = event.message
            if (
                message is not None
                and message.chat.type == ChatType.PRIVATE
                and message.chat.id == event.from_user.id
            ):
                user_id = event.from_user.id
        if user_id is not None:
            async with self._session_factory() as session:
                await record_telegram_user(session, user_id)
                await session.commit()
        return await handler(event, data)


@dataclass(frozen=True, slots=True)
class CustomColorUploadState:
    color_structure: str | None = None
    finish: str | None = None


CustomColorUploads = dict[int, CustomColorUploadState]


def _trusted_private_message(message: Message) -> bool:
    return (
        message.chat.type == ChatType.PRIVATE
        and message.from_user is not None
        and message.chat.id == message.from_user.id
    )


def _format_number(value: float) -> str:
    return f"{value:g}"


def rejection_copy(
    rejection: MediaRejection,
    settings: AppSettings,
) -> str:
    """Map stable media codes to exact, configured recovery guidance."""

    if rejection.code is MediaRejectionCode.UNSUPPORTED_FORMAT:
        labels = {
            "image/jpeg": "JPEG",
            "image/png": "PNG",
            "image/webp": "WebP",
        }
        allowed = ", ".join(
            labels[mime_type] for mime_type in settings.document_mime_allowlist
        )
        return (
            "Этот формат не поддерживается. Отправьте изображение "
            f"в одном из форматов: {allowed}."
        )
    if rejection.code is MediaRejectionCode.TOO_LARGE:
        maximum_mb = settings.max_media_bytes / (1024 * 1024)
        return (
            f"Файл больше {_format_number(maximum_mb)} МБ. "
            "Отправьте изображение меньшего размера. Сжать бесплатно фото можно тут - https://www.iloveimg.com/ru/compress-image"
        )
    if rejection.code is MediaRejectionCode.DIMENSION_LIMIT:
        return (
            "Размер стороны должен быть от "
            f"{settings.min_side_px} до {settings.max_side_px} пикселей. "
            "Измените размер изображения и отправьте его снова."
        )
    if rejection.code is MediaRejectionCode.PIXEL_LIMIT:
        maximum_megapixels = settings.max_pixels / 1_000_000
        return (
            f"В изображении больше {_format_number(maximum_megapixels)} Мп. "
            "Уменьшите разрешение и отправьте его снова."
        )
    if rejection.code is MediaRejectionCode.DOWNLOAD_FAILED:
        return "Не удалось получить фото из Telegram. Отправьте его ещё раз."
    return "Не удалось прочитать изображение. Отправьте другой файл."


def palette_keyboard(
    *,
    text: str,
    settings: AppSettings,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text,
                    web_app=WebAppInfo(url=settings.mini_app_url),
                )
            ]
        ]
    )


def menu_keyboard(settings: AppSettings) -> InlineKeyboardMarkup:
    """Keep the primary actions and common service information in one menu."""

    support_button = (
        InlineKeyboardButton(
            text="💬 Поддержка",
            url=settings.ultima_manager_contact_url,
        )
        if settings.ultima_manager_contact_url
        else InlineKeyboardButton(
            text="💬 Поддержка", callback_data=f"{INFO_CALLBACK_PREFIX}{INFO_SUPPORT}"
        )
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎨 Сгенерировать",
                    web_app=WebAppInfo(url=settings.mini_app_url),
                )
            ],
            [
                InlineKeyboardButton(
                    text="ℹ️ Как это работает",
                    callback_data=f"{INFO_CALLBACK_PREFIX}{INFO_HOW_TO}",
                ),
                InlineKeyboardButton(
                    text="💳 Тарифы",
                    callback_data=f"{INFO_CALLBACK_PREFIX}{INFO_PRICES}",
                ),
            ],
            [
                support_button,
                InlineKeyboardButton(
                    text="🔒 Конфиденциальность",
                    callback_data=f"{INFO_CALLBACK_PREFIX}{INFO_PRIVACY}",
                ),
            ],
        ]
    )


def replace_photo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="← Отмена",
                    callback_data=REPLACE_PHOTO_CANCEL_CALLBACK_DATA,
                )
            ]
        ]
    )


def paywall_keyboard(
    *,
    intro_available: bool,
    intro_checkout_url: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if intro_available:
        intro_button = (
            InlineKeyboardButton(text="💳 1 генерация — 25 ₽", url=intro_checkout_url)
            if intro_checkout_url is not None
            else InlineKeyboardButton(
                text="💳 1 генерация — 25 ₽",
                callback_data=f"{BILLING_PRODUCT_PREFIX}intro_25",
            )
        )
        rows.append([intro_button])
    rows.append(
        [
            InlineKeyboardButton(
                text="📦 Пакеты", callback_data=PACKAGES_CALLBACK_DATA
            ),
            InlineKeyboardButton(text="🗓️ Месяц", callback_data=MONTHLY_CALLBACK_DATA),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⛔ Отключить автопродление",
                    callback_data=BILLING_CANCEL_CALLBACK_DATA,
                )
            ]
        ]
    )


def intro_recurring_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить сохранённую карту",
                    callback_data=BILLING_CANCEL_INTRO_RECURRING_CALLBACK_DATA,
                )
            ]
        ]
    )


def intro_card_keyboard(
    *,
    source_id: UUID,
    other_checkout_url: str | None,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="💳 Сохранённая карта",
                callback_data=f"{BILLING_INTRO_SAVED_PREFIX}{source_id}",
            )
        ]
    ]
    if other_checkout_url is not None:
        rows.append(
            [InlineKeyboardButton(text="💳 Другая карта", url=other_checkout_url)]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="← Назад",
                callback_data=BILLING_BACK_CALLBACK_DATA,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def package_keyboard(
    checkout_urls: Mapping[str, str] | None = None,
) -> InlineKeyboardMarkup:
    urls = checkout_urls or {}

    def package_button(product_id: str, text: str) -> InlineKeyboardButton:
        checkout_url = urls.get(product_id)
        if checkout_url is not None:
            return InlineKeyboardButton(text=text, url=checkout_url)
        return InlineKeyboardButton(
            text=text,
            callback_data=f"{BILLING_PRODUCT_PREFIX}{product_id}",
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [package_button("pack_5", "📦 5 генераций — 149 ₽")],
            [package_button("pack_15", "📦 15 генераций — 349 ₽")],
            [package_button("pack_40", "📦 40 генераций — 749 ₽")],
            [
                InlineKeyboardButton(
                    text="← Назад", callback_data=BILLING_BACK_CALLBACK_DATA
                )
            ],
        ]
    )


def monthly_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗓️ Plus: 30 генераций — 499 ₽/месяц",
                    callback_data=f"{BILLING_PRODUCT_PREFIX}plus",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗓️ Studio: 100 генераций — 1 499 ₽/месяц",
                    callback_data=f"{BILLING_PRODUCT_PREFIX}studio",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💎 Ultima — Узнать цену", callback_data=ULTIMA_CALLBACK_DATA
                )
            ],
            [
                InlineKeyboardButton(
                    text="← Назад", callback_data=BILLING_BACK_CALLBACK_DATA
                )
            ],
        ]
    )


def _trusted_callback(callback: CallbackQuery) -> int | None:
    message = callback.message
    if (
        message is None
        or message.chat.type != ChatType.PRIVATE
        or message.chat.id != callback.from_user.id
    ):
        return None
    return callback.from_user.id


async def _intro_available(session: AsyncSession, user_id: int) -> bool:
    return await BillingRepository().intro_offer_available(session, user_id=user_id)


async def handle_paywall_callback(
    callback: CallbackQuery,
    *,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    payment_service: PaymentProcessor | None,
) -> None:
    await bot.answer_callback_query(callback.id)
    user_id = _trusted_callback(callback)
    if user_id is None or callback.message is None:
        return
    await send_paywall(
        bot,
        chat_id=callback.message.chat.id,
        user_id=user_id,
        session_factory=session_factory,
        payment_service=payment_service,
    )


async def _create_checkout_url(
    *,
    payment_service: PaymentProcessor | None,
    user_id: int,
    product_id: str,
) -> str | None:
    """Issue a one-off checkout link after an explicit product action."""

    if payment_service is None or not payment_service.production_available():
        return None
    try:
        _order, url = await payment_service.start_checkout(
            user_id=user_id,
            product_id=product_id,
            idempotency_key=uuid4().hex,
        )
    except PaymentActivationDenied:
        logger.warning("direct checkout blocked by production gate")
        return None
    except Exception:
        logger.exception("failed to create direct checkout")
        return None
    return str(url)


async def send_paywall(
    bot: Bot,
    *,
    chat_id: int,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    payment_service: PaymentProcessor | None = None,
) -> None:
    """Render the only authenticated entry to product navigation and renewal control."""

    async with session_factory() as session:
        intro_available = await _intro_available(session, user_id)
        subscription = await session.scalar(
            select(Subscription.id)
            .where(
                Subscription.telegram_user_id == user_id,
                Subscription.status == "active",
            )
            .limit(1)
        )
    intro_source = (
        await payment_service.active_intro_recurring_source(user_id=user_id)
        if payment_service is not None
        else None
    )
    intro_checkout_url = (
        await _create_checkout_url(
            payment_service=payment_service,
            user_id=user_id,
            product_id="intro_25",
        )
        if intro_source is None
        else None
    )
    await bot.send_message(
        chat_id,
        "Выберите вариант:",
        reply_markup=paywall_keyboard(
            intro_available=intro_available,
            intro_checkout_url=intro_checkout_url,
        ),
    )
    if subscription is not None:
        await bot.send_message(
            chat_id,
            "Автопродление подписки активно.",
            reply_markup=subscription_cancel_keyboard(),
        )
    if intro_source is not None:
        await bot.send_message(
            chat_id,
            "Для быстрых покупок доступна сохранённая карта.",
            reply_markup=intro_recurring_cancel_keyboard(),
        )


async def handle_billing_command(
    message: Message,
    *,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    payment_service: PaymentProcessor | None = None,
) -> None:
    """Open the authenticated billing and subscription-management screen."""

    if not _trusted_private_message(message) or message.from_user is None:
        return
    await send_paywall(
        bot,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        session_factory=session_factory,
        payment_service=payment_service,
    )


async def handle_billing_navigation(
    callback: CallbackQuery,
    *,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    payment_service: PaymentProcessor | None,
    screen: str,
) -> None:
    await bot.answer_callback_query(callback.id)
    user_id = _trusted_callback(callback)
    if user_id is None or callback.message is None:
        return
    if screen == "packages":
        product_ids = ("pack_5", "pack_15", "pack_40")
        urls = await asyncio.gather(
            *(
                _create_checkout_url(
                    payment_service=payment_service,
                    user_id=user_id,
                    product_id=product_id,
                )
                for product_id in product_ids
            )
        )
        checkout_urls = {
            product_id: url
            for product_id, url in zip(product_ids, urls, strict=True)
            if url is not None
        }
        text, keyboard = "Пакеты генераций:", package_keyboard(checkout_urls)
    elif screen == "monthly":
        text, keyboard = "Месячные планы:", monthly_keyboard()
    else:
        async with session_factory() as session:
            intro_available = await _intro_available(session, user_id)
        intro_source = (
            await payment_service.active_intro_recurring_source(user_id=user_id)
            if payment_service is not None
            else None
        )
        intro_checkout_url = (
            await _create_checkout_url(
                payment_service=payment_service,
                user_id=user_id,
                product_id="intro_25",
            )
            if intro_available and intro_source is None
            else None
        )
        text, keyboard = (
            "Выберите вариант:",
            paywall_keyboard(
                intro_available=intro_available,
                intro_checkout_url=intro_checkout_url,
            ),
        )
    await bot.edit_message_text(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        text=text,
        reply_markup=keyboard,
    )


async def _send_checkout(
    callback: CallbackQuery,
    *,
    bot: Bot,
    payment_service: PaymentProcessor,
    product_id: str,
    consented_at: datetime | None,
) -> None:
    user_id = _trusted_callback(callback)
    if user_id is None or callback.message is None:
        return
    if not payment_service.production_available():
        await bot.send_message(callback.message.chat.id, PAYMENTS_UNAVAILABLE_COPY)
        return
    try:
        _order, url = await payment_service.start_checkout(
            user_id=user_id,
            product_id=product_id,
            idempotency_key=uuid4().hex,
            recurring_consent_at=consented_at,
        )
    except PaymentActivationDenied:
        logger.warning("checkout blocked by production gate")
        await bot.send_message(callback.message.chat.id, PAYMENTS_UNAVAILABLE_COPY)
        return
    except Exception:
        logger.exception("checkout failed before Robokassa confirmation")
        await bot.send_message(callback.message.chat.id, PAYMENTS_UNAVAILABLE_COPY)
        return
    await bot.send_message(
        callback.message.chat.id,
        "Перейдите к оплате",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💳 Оплатить", url=url)]]
        ),
    )


async def handle_billing_product(
    callback: CallbackQuery, *, bot: Bot, payment_service: PaymentProcessor
) -> None:
    await bot.answer_callback_query(callback.id)
    product_id = (callback.data or "")[len(BILLING_PRODUCT_PREFIX) :]
    user_id = _trusted_callback(callback)
    if user_id is None or callback.message is None:
        return
    try:
        product = get_product(product_id)
    except ValueError:
        return
    if product.kind is ProductKind.INTRO:
        source = await payment_service.active_intro_recurring_source(user_id=user_id)
        if source is None:
            await _send_checkout(
                callback,
                bot=bot,
                payment_service=payment_service,
                product_id=product.id.value,
                consented_at=None,
            )
            return
        other_checkout_url = await _create_checkout_url(
            payment_service=payment_service,
            user_id=user_id,
            product_id=product.id.value,
        )
        await bot.send_message(
            callback.message.chat.id,
            "Выберите карту для оплаты:",
            reply_markup=intro_card_keyboard(
                source_id=source.id,
                other_checkout_url=other_checkout_url,
            ),
        )
        return
    if product.kind is ProductKind.MONTHLY:
        amount = product.amount_kopecks // 100 if product.amount_kopecks else 0
        await bot.send_message(
            callback.message.chat.id,
            f"{amount:,} ₽ в месяц. Нажимая кнопку ниже, вы соглашаетесь на "
            "автоматическое ежемесячное списание этой суммы. Отменить "
            "автопродление можно в любой момент по команде /billing",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Согласен на автопродление",
                            callback_data=f"{BILLING_CONSENT_PREFIX}{product.id.value}",
                        )
                    ]
                ]
            ),
        )
        return
    await _send_checkout(
        callback,
        bot=bot,
        payment_service=payment_service,
        product_id=product.id.value,
        consented_at=None,
    )


async def handle_intro_saved_card(
    callback: CallbackQuery,
    *,
    bot: Bot,
    payment_service: PaymentProcessor,
) -> None:
    await bot.answer_callback_query(callback.id)
    user_id = _trusted_callback(callback)
    if user_id is None or callback.message is None:
        return
    try:
        source_id = UUID(
            (callback.data or "")[len(BILLING_INTRO_SAVED_PREFIX) :]
        )
    except ValueError:
        return
    try:
        order = await payment_service.start_intro_recurring_charge(
            user_id=user_id,
            source_id=source_id,
        )
    except PaymentActivationDenied:
        await bot.send_message(callback.message.chat.id, PAYMENTS_UNAVAILABLE_COPY)
        return
    except Exception:
        logger.exception("intro recurring charge failed")
        await bot.send_message(
            callback.message.chat.id,
            "Не удалось списать 25 ₽. Попробуйте ещё раз позже.",
        )
        return
    if order is None:
        await bot.send_message(
            callback.message.chat.id,
            "Платёж уже обрабатывается или сохранённая карта недоступна.",
        )
        return
    await bot.send_message(
        callback.message.chat.id,
        "Производим оплату. Генерация будет начислена после подтверждения "
        "оплаты от банка.",
    )


async def handle_monthly_consent(
    callback: CallbackQuery, *, bot: Bot, payment_service: PaymentProcessor
) -> None:
    await bot.answer_callback_query(callback.id)
    product_id = (callback.data or "")[len(BILLING_CONSENT_PREFIX) :]
    try:
        product = get_product(product_id)
    except ValueError:
        return
    if product.kind is ProductKind.MONTHLY:
        await _send_checkout(
            callback,
            bot=bot,
            payment_service=payment_service,
            product_id=product.id.value,
            consented_at=datetime.now(UTC),
        )


async def handle_ultima(
    callback: CallbackQuery,
    *,
    bot: Bot,
    settings: AppSettings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await bot.answer_callback_query(callback.id)
    user_id = _trusted_callback(callback)
    if (
        user_id is None
        or callback.message is None
        or settings.ultima_manager_contact_url is None
    ):
        return
    async with session_factory() as session:
        session.add(UltimaLead(telegram_user_id=user_id))
        await session.commit()
    await bot.send_message(
        callback.message.chat.id,
        ULTIMA_COPY,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с менеджером",
                        url=settings.ultima_manager_contact_url,
                    )
                ]
            ]
        ),
    )


async def handle_subscription_cancel(
    callback: CallbackQuery,
    *,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await bot.answer_callback_query(callback.id)
    user_id = _trusted_callback(callback)
    if user_id is None or callback.message is None:
        return
    async with session_factory() as session:
        subscription = await session.scalar(
            select(Subscription)
            .where(
                Subscription.telegram_user_id == user_id,
                Subscription.status == "active",
            )
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        if subscription is not None:
            subscription.status = "cancelled"
            subscription.cancelled_at = datetime.now(UTC)
            await session.commit()
    await bot.send_message(
        callback.message.chat.id,
        "Автопродление отключено. Купленные пакеты генераций сохранены.",
    )


async def handle_intro_recurring_cancel(
    callback: CallbackQuery,
    *,
    bot: Bot,
    payment_service: PaymentProcessor,
) -> None:
    await bot.answer_callback_query(callback.id)
    user_id = _trusted_callback(callback)
    if user_id is None or callback.message is None:
        return
    if await payment_service.cancel_intro_recurring_source(user_id=user_id):
        await bot.send_message(
            callback.message.chat.id,
            "Сохранённая карта удалена из вариантов оплаты.",
        )


def custom_color_structure_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Однотонная",
                    callback_data=f"{CUSTOM_COLOR_STRUCTURE_PREFIX}solid",
                ),
                InlineKeyboardButton(
                    text="Многоцветная",
                    callback_data=f"{CUSTOM_COLOR_STRUCTURE_PREFIX}multicolor",
                ),
            ]
        ]
    )


def custom_color_finish_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Матовая",
                    callback_data=f"{CUSTOM_COLOR_FINISH_PREFIX}matte",
                ),
                InlineKeyboardButton(
                    text="Сатин",
                    callback_data=f"{CUSTOM_COLOR_FINISH_PREFIX}satin",
                ),
                InlineKeyboardButton(
                    text="Глянцевая",
                    callback_data=f"{CUSTOM_COLOR_FINISH_PREFIX}gloss",
                ),
            ]
        ]
    )


async def handle_start_message(
    message: Message,
    *,
    bot: Bot,
    settings: AppSettings | None = None,
    command: CommandObject | None = None,
    pending_uploads: CustomColorUploads | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    payment_service: PaymentProcessor | None = None,
) -> None:
    if not _trusted_private_message(message):
        return
    if (
        command is not None
        and command.args == "custom_color"
        and pending_uploads is not None
    ):
        await handle_custom_color_request(
            message,
            bot=bot,
            pending_uploads=pending_uploads,
        )
        return
    if command is not None and command.args == "billing":
        if session_factory is None:
            raise RuntimeError("session factory is required for a billing launch")
        await handle_billing_command(
            message,
            bot=bot,
            session_factory=session_factory,
            payment_service=payment_service,
        )
        return
    if command is not None and command.args == "open_app":
        if settings is None:
            raise RuntimeError("settings are required for an app-launch command")
        await bot.send_message(
            message.chat.id,
            OPEN_APP_COPY,
            reply_markup=palette_keyboard(
                text="🚀 Открыть приложение", settings=settings
            ),
        )
        return
    await bot.send_message(message.chat.id, NO_SOURCE_COPY)


async def handle_custom_color_request(
    message: Message,
    *,
    bot: Bot,
    pending_uploads: CustomColorUploads,
) -> None:
    if not _trusted_private_message(message) or message.from_user is None:
        return
    pending_uploads[message.from_user.id] = CustomColorUploadState()
    await bot.send_message(
        message.chat.id,
        CUSTOM_COLOR_STRUCTURE_COPY,
        reply_markup=custom_color_structure_keyboard(),
    )


async def handle_custom_color_structure(
    callback: CallbackQuery,
    *,
    bot: Bot,
    pending_uploads: CustomColorUploads,
) -> None:
    await bot.answer_callback_query(callback.id)
    message = callback.message
    if (
        message is None
        or message.chat.type != ChatType.PRIVATE
        or message.chat.id != callback.from_user.id
    ):
        return
    value = (callback.data or "")[len(CUSTOM_COLOR_STRUCTURE_PREFIX) :]
    if value not in {"solid", "multicolor"}:
        return
    current = pending_uploads.get(callback.from_user.id, CustomColorUploadState())
    pending_uploads[callback.from_user.id] = replace(
        current,
        color_structure=value,
        finish=None,
    )
    await bot.send_message(
        message.chat.id,
        CUSTOM_COLOR_FINISH_COPY,
        reply_markup=custom_color_finish_keyboard(),
    )


async def handle_custom_color_finish(
    callback: CallbackQuery,
    *,
    bot: Bot,
    pending_uploads: CustomColorUploads,
) -> None:
    await bot.answer_callback_query(callback.id)
    message = callback.message
    if (
        message is None
        or message.chat.type != ChatType.PRIVATE
        or message.chat.id != callback.from_user.id
    ):
        return
    value = (callback.data or "")[len(CUSTOM_COLOR_FINISH_PREFIX) :]
    current = pending_uploads.get(callback.from_user.id)
    if value not in {"matte", "satin", "gloss"} or current is None:
        return
    if current.color_structure not in {"solid", "multicolor"}:
        await bot.send_message(
            message.chat.id,
            CUSTOM_COLOR_STRUCTURE_COPY,
            reply_markup=custom_color_structure_keyboard(),
        )
        return
    pending_uploads[callback.from_user.id] = replace(current, finish=value)
    await bot.send_message(message.chat.id, CUSTOM_COLOR_REQUEST_COPY)


async def _edit_progress(
    bot: Bot,
    *,
    chat_id: int,
    message_id: int,
    percent: int,
) -> None:
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=CUSTOM_COLOR_PROCESSING_COPY.format(percent=percent),
        )
    except Exception:
        return


async def _await_with_analysis_progress(
    operation: Awaitable[Any],
    *,
    bot: Bot,
    chat_id: int,
    message_id: int,
    interval_seconds: float,
) -> Any:
    task = asyncio.ensure_future(operation)
    try:
        for percent in (50, 70, 82):
            try:
                return await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=interval_seconds,
                )
            except TimeoutError:
                await _edit_progress(
                    bot,
                    chat_id=chat_id,
                    message_id=message_id,
                    percent=percent,
                )
        return await task
    except BaseException:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        raise


async def handle_custom_color_message(
    message: Message,
    *,
    bot: Bot,
    settings: AppSettings,
    session_factory: async_sessionmaker[AsyncSession],
    custom_color_service: CustomColorCreator,
    pending_uploads: CustomColorUploads,
    progress_interval_seconds: float = 2.0,
    progress_completion_pause_seconds: float = 0.7,
) -> None:
    if not _trusted_private_message(message) or message.from_user is None:
        return
    owner_id = message.from_user.id
    upload_state = pending_uploads.get(owner_id)
    if upload_state is None or upload_state.color_structure is None:
        await bot.send_message(
            message.chat.id,
            CUSTOM_COLOR_STRUCTURE_COPY,
            reply_markup=custom_color_structure_keyboard(),
        )
        return
    if upload_state.finish is None:
        await bot.send_message(
            message.chat.id,
            CUSTOM_COLOR_FINISH_COPY,
            reply_markup=custom_color_finish_keyboard(),
        )
        return
    if not isinstance(message.document, Document):
        await bot.send_message(
            message.chat.id,
            "Отправьте образец именно как файл, а не как сжатое фото.",
            reply_to_message_id=message.message_id,
        )
        return
    progress = await bot.send_message(
        message.chat.id,
        CUSTOM_COLOR_PROCESSING_COPY.format(percent=35),
        reply_to_message_id=message.message_id,
    )
    progress_id = getattr(progress, "message_id", None)
    if not isinstance(progress_id, int):
        progress_id = message.message_id
    try:
        downloaded = await read_supported_media_bytes(
            bot, message.document, settings=settings
        )

        async def create_color() -> Any:
            async with session_factory() as session:
                return await custom_color_service.create(
                    session,
                    owner_id=owner_id,
                    display_name="",
                    upload=downloaded.data,
                    declared_mime=downloaded.mime_type,
                    idempotency_key=f"bot-{message.chat.id}-{message.message_id}",
                    color_structure=upload_state.color_structure,
                    finish=upload_state.finish,
                )

        color = await _await_with_analysis_progress(
            create_color(),
            bot=bot,
            chat_id=message.chat.id,
            message_id=progress_id,
            interval_seconds=progress_interval_seconds,
        )
        await _edit_progress(
            bot, chat_id=message.chat.id, message_id=progress_id, percent=100
        )
        await asyncio.sleep(progress_completion_pause_seconds)
        del pending_uploads[owner_id]
    except (MediaRejection, ValueError, RuntimeError):
        await bot.send_message(
            message.chat.id,
            CUSTOM_COLOR_FAILED_COPY,
            reply_to_message_id=message.message_id,
        )
        return
    status_value = getattr(color, "status", "")
    name = getattr(color, "display_name", "Без названия")
    if status_value != "approved":
        pending_copy = (
            CUSTOM_COLOR_RETAKE_COPY
            if getattr(color, "reason_code", None) == "reference_analysis_uncertain"
            else CUSTOM_COLOR_PENDING_COPY
        )
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=progress_id,
            text=pending_copy,
        )
        return
    selection_id = custom_selection_id(
        UUID(str(color.id)), int(getattr(color, "current_version", 1))
    )
    await bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=progress_id,
        text=CUSTOM_COLOR_READY_COPY.format(name=name),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✨ Запустить генерацию с этим цветом",
                        callback_data=f"{CUSTOM_COLOR_GENERATE_PREFIX}{selection_id}",
                    )
                ]
            ]
        ),
    )


async def handle_custom_color_generate(
    callback: CallbackQuery,
    *,
    bot: Bot,
    job_acceptance_service: JobAcceptor,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await bot.answer_callback_query(callback.id)
    message = callback.message
    if (
        message is None
        or message.chat.type != ChatType.PRIVATE
        or message.chat.id != callback.from_user.id
    ):
        return
    color_id = (callback.data or "")[len(CUSTOM_COLOR_GENERATE_PREFIX) :]
    try:
        async with session_factory() as session:
            await job_acceptance_service.accept(
                session,
                user_id=callback.from_user.id,
                color_id=color_id,
                submission_uuid=uuid4(),
            )
    except Exception:
        await bot.send_message(
            message.chat.id,
            "Не удалось запустить генерацию. Откройте палитру и попробуйте ещё раз.",
        )
        return


async def handle_unsupported_message(message: Message, *, bot: Bot) -> None:
    if not _trusted_private_message(message):
        return
    await bot.send_message(message.chat.id, UNSUPPORTED_MESSAGE_COPY)


async def handle_menu_callback(
    callback: CallbackQuery,
    *,
    bot: Bot,
    settings: AppSettings,
) -> None:
    await bot.answer_callback_query(callback.id)
    message = callback.message
    if (
        message is None
        or message.chat.type != ChatType.PRIVATE
        or message.chat.id != callback.from_user.id
    ):
        return
    await bot.send_message(
        message.chat.id,
        "Меню",
        reply_markup=menu_keyboard(settings),
    )


async def handle_info_callback(callback: CallbackQuery, *, bot: Bot) -> None:
    """Send concise, standard service information from the bot menu."""

    await bot.answer_callback_query(callback.id)
    message = callback.message
    if (
        message is None
        or message.chat.type != ChatType.PRIVATE
        or message.chat.id != callback.from_user.id
    ):
        return
    section = (callback.data or "")[len(INFO_CALLBACK_PREFIX) :]
    copy = {
        INFO_HOW_TO: (
            "Как это работает:\n\n"
            "1. Отправьте фото автомобиля или мотоцикла.\n"
            "2. Откройте палитру и выберите цвет.\n"
            "3. Получите AI-визуализацию в этом чате."
        ),
        INFO_PRICES: (
            "Тарифы:\n\n"
            "1 генерация — 25 ₽\n"
            "Пакеты: 5 — 149 ₽, 15 — 349 ₽, 40 — 749 ₽\n"
            "Plus: 30 — 499 ₽/месяц\n"
            "Studio: 100 — 1 499 ₽/месяц"
        ),
        INFO_SUPPORT: (
            "Поддержка:\n\n"
            "Опишите вопрос сообщением в этом чате. Если он связан с оплатой, "
            "укажите дату и сумму платежа."
        ),
        INFO_PRIVACY: (
            "Конфиденциальность:\n\n"
            "Мы не сохраняем файлы изображений на сервере. Фото обрабатываются "
            "Telegram и AI-провайдером только для создания визуализации."
        ),
    }.get(section)
    if copy is not None:
        await bot.send_message(message.chat.id, copy)


async def handle_replace_photo_cancel_callback(
    callback: CallbackQuery,
    *,
    bot: Bot,
    settings: AppSettings,
) -> None:
    await bot.answer_callback_query(callback.id)
    message = callback.message
    if (
        message is None
        or message.chat.type != ChatType.PRIVATE
        or message.chat.id != callback.from_user.id
    ):
        return
    await bot.send_message(
        message.chat.id,
        WINNING_SOURCE_COPY,
        reply_markup=palette_keyboard(text="🎨 Выбрать цвет", settings=settings),
    )


async def handle_media_message(
    message: Message,
    *,
    bot: Bot,
    settings: AppSettings,
    session_factory: async_sessionmaker[AsyncSession],
    active_source_setter: ActiveSourceSetter = set_active_source,
) -> None:
    """Validate one trusted message and acknowledge its canonical outcome."""

    if not _trusted_private_message(message):
        return
    sender = message.from_user
    if sender is None:
        return
    media = (
        max(message.photo, key=lambda item: item.width * item.height)
        if message.photo
        else message.document
    )
    if media is None:
        await handle_unsupported_message(message, bot=bot)
        return
    try:
        accepted = await read_supported_media(bot, media, settings=settings)
    except MediaRejection as rejection:
        await bot.send_message(
            message.chat.id,
            rejection_copy(rejection, settings),
            reply_to_message_id=message.message_id,
        )
        return

    async with session_factory() as session:
        decision = await active_source_setter(
            session,
            accepted,
            telegram_user_id=sender.id,
            chat_id=message.chat.id,
            source_message_id=message.message_id,
        )
        await session.commit()

    text = WINNING_SOURCE_COPY if decision.became_active else OLDER_SOURCE_COPY
    button_text = (
        "Выбрать цвет" if decision.became_active else "Выбрать цвет для активного фото"
    )
    await bot.send_message(
        message.chat.id,
        text,
        reply_to_message_id=message.message_id,
        reply_markup=palette_keyboard(text=button_text, settings=settings),
    )


def create_router(
    *,
    settings: AppSettings,
    session_factory: async_sessionmaker[AsyncSession],
    custom_color_service: CustomColorCreator = None,
    job_acceptance_service: JobAcceptor = None,
    payment_service: PaymentProcessor = None,
) -> Router:
    """Build the three ordered private-chat handlers."""

    router = Router(name="car-wrap-private-ingress")
    pending_custom_color_uploads: CustomColorUploads = {}

    user_tracking = UserTrackingMiddleware(session_factory)
    router.message.outer_middleware(user_tracking)
    router.callback_query.outer_middleware(user_tracking)

    @router.callback_query(F.data == PAYWALL_CALLBACK_DATA)
    async def paywall_handler(callback: CallbackQuery, bot: Bot) -> None:
        await handle_paywall_callback(
            callback,
            bot=bot,
            session_factory=session_factory,
            payment_service=payment_service,
        )

    @router.callback_query(F.data == PACKAGES_CALLBACK_DATA)
    async def packages_handler(callback: CallbackQuery, bot: Bot) -> None:
        await handle_billing_navigation(
            callback,
            bot=bot,
            session_factory=session_factory,
            payment_service=payment_service,
            screen="packages",
        )

    @router.callback_query(F.data == MONTHLY_CALLBACK_DATA)
    async def monthly_handler(callback: CallbackQuery, bot: Bot) -> None:
        await handle_billing_navigation(
            callback,
            bot=bot,
            session_factory=session_factory,
            payment_service=payment_service,
            screen="monthly",
        )

    @router.callback_query(F.data == BILLING_BACK_CALLBACK_DATA)
    async def billing_back_handler(callback: CallbackQuery, bot: Bot) -> None:
        await handle_billing_navigation(
            callback,
            bot=bot,
            session_factory=session_factory,
            payment_service=payment_service,
            screen="back",
        )

    @router.callback_query(F.data == ULTIMA_CALLBACK_DATA)
    async def ultima_handler(callback: CallbackQuery, bot: Bot) -> None:
        await handle_ultima(
            callback, bot=bot, settings=settings, session_factory=session_factory
        )

    @router.callback_query(F.data == BILLING_CANCEL_CALLBACK_DATA)
    async def billing_cancel_handler(callback: CallbackQuery, bot: Bot) -> None:
        await handle_subscription_cancel(
            callback, bot=bot, session_factory=session_factory
        )

    @router.callback_query(F.data == BILLING_CANCEL_INTRO_RECURRING_CALLBACK_DATA)
    async def intro_recurring_cancel_handler(callback: CallbackQuery, bot: Bot) -> None:
        if payment_service is not None:
            await handle_intro_recurring_cancel(
                callback,
                bot=bot,
                payment_service=payment_service,
            )

    @router.callback_query(F.data.startswith(BILLING_CONSENT_PREFIX))
    async def billing_consent_handler(callback: CallbackQuery, bot: Bot) -> None:
        if payment_service is not None:
            await handle_monthly_consent(
                callback, bot=bot, payment_service=payment_service
            )

    @router.callback_query(F.data.startswith(BILLING_INTRO_SAVED_PREFIX))
    async def intro_saved_card_handler(callback: CallbackQuery, bot: Bot) -> None:
        if payment_service is not None:
            await handle_intro_saved_card(
                callback,
                bot=bot,
                payment_service=payment_service,
            )

    @router.callback_query(F.data.startswith(BILLING_PRODUCT_PREFIX))
    async def billing_product_handler(callback: CallbackQuery, bot: Bot) -> None:
        if payment_service is not None:
            await handle_billing_product(
                callback, bot=bot, payment_service=payment_service
            )

    @router.callback_query(F.data == CUSTOM_COLOR_REQUEST_CALLBACK_DATA)
    async def custom_color_request_handler(callback: CallbackQuery, bot: Bot) -> None:
        await bot.answer_callback_query(callback.id)
        if isinstance(callback.message, Message):
            await handle_custom_color_request(
                callback.message,
                bot=bot,
                pending_uploads=pending_custom_color_uploads,
            )

    @router.callback_query(F.data.startswith(CUSTOM_COLOR_STRUCTURE_PREFIX))
    async def custom_color_structure_handler(
        callback: CallbackQuery,
        bot: Bot,
    ) -> None:
        await handle_custom_color_structure(
            callback,
            bot=bot,
            pending_uploads=pending_custom_color_uploads,
        )

    @router.callback_query(F.data.startswith(CUSTOM_COLOR_FINISH_PREFIX))
    async def custom_color_finish_handler(
        callback: CallbackQuery,
        bot: Bot,
    ) -> None:
        await handle_custom_color_finish(
            callback,
            bot=bot,
            pending_uploads=pending_custom_color_uploads,
        )

    @router.callback_query(F.data.startswith(CUSTOM_COLOR_GENERATE_PREFIX))
    async def custom_color_generate_handler(callback: CallbackQuery, bot: Bot) -> None:
        if job_acceptance_service is None:
            await bot.answer_callback_query(callback.id, show_alert=True)
            return
        await handle_custom_color_generate(
            callback,
            bot=bot,
            job_acceptance_service=job_acceptance_service,
            session_factory=session_factory,
        )

    @router.callback_query(F.data == MENU_CALLBACK_DATA)
    async def menu_handler(callback: CallbackQuery, bot: Bot) -> None:
        await handle_menu_callback(callback, bot=bot, settings=settings)

    @router.callback_query(F.data.startswith(INFO_CALLBACK_PREFIX))
    async def info_handler(callback: CallbackQuery, bot: Bot) -> None:
        await handle_info_callback(callback, bot=bot)

    @router.callback_query(F.data == REPLACE_PHOTO_CANCEL_CALLBACK_DATA)
    async def replace_photo_cancel_handler(
        callback: CallbackQuery,
        bot: Bot,
    ) -> None:
        await handle_replace_photo_cancel_callback(
            callback,
            bot=bot,
            settings=settings,
        )

    @router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
    async def start_handler(
        message: Message,
        bot: Bot,
        command: CommandObject,
    ) -> None:
        await handle_start_message(
            message,
            bot=bot,
            settings=settings,
            command=command,
            pending_uploads=pending_custom_color_uploads,
            session_factory=session_factory,
            payment_service=payment_service,
        )

    @router.message(Command("billing"), F.chat.type == ChatType.PRIVATE)
    async def billing_command_handler(message: Message, bot: Bot) -> None:
        await handle_billing_command(
            message,
            bot=bot,
            session_factory=session_factory,
            payment_service=payment_service,
        )

    @router.message(
        F.chat.type == ChatType.PRIVATE,
        F.photo | F.document,
    )
    async def media_handler(message: Message, bot: Bot) -> None:
        if (
            custom_color_service is not None
            and message.from_user is not None
            and message.from_user.id in pending_custom_color_uploads
        ):
            await handle_custom_color_message(
                message,
                bot=bot,
                settings=settings,
                session_factory=session_factory,
                custom_color_service=custom_color_service,
                pending_uploads=pending_custom_color_uploads,
            )
            return
        await handle_media_message(
            message,
            bot=bot,
            settings=settings,
            session_factory=session_factory,
        )

    @router.message(F.chat.type == ChatType.PRIVATE)
    async def fallback_handler(message: Message, bot: Bot) -> None:
        await handle_unsupported_message(message, bot=bot)

    return router
