"""Private-chat Telegram intake and truthful active-source replies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from car_wrap.bot.media import (
    MediaRejection,
    MediaRejectionCode,
    read_supported_media,
)
from car_wrap.config import AppSettings
from car_wrap.services.active_source import (
    ActiveSourceDecision,
    set_active_source,
)

NO_SOURCE_COPY = (
    "Отправьте фото автомобиля или мотоцикла. Лучше всего подойдёт чёткий "
    "кадр, где хорошо видны окрашенные части."
)
UNSUPPORTED_MESSAGE_COPY = (
    "Нужна фотография автомобиля или мотоцикла. "
    "Отправьте фото или изображение файлом."
)
WINNING_SOURCE_COPY = (
    "Это фото выбрано для оклейки. Теперь выберите цвет."
)
OLDER_SOURCE_COPY = (
    "Фото принято, но для оклейки уже выбрано более новое фото."
)

ActiveSourceSetter = Callable[..., Awaitable[ActiveSourceDecision]]


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
            labels[mime_type]
            for mime_type in settings.document_mime_allowlist
        )
        return (
            "Этот формат не поддерживается. Отправьте изображение "
            f"в одном из форматов: {allowed}."
        )
    if rejection.code is MediaRejectionCode.TOO_LARGE:
        maximum_mb = settings.max_media_bytes / (1024 * 1024)
        return (
            f"Файл больше {_format_number(maximum_mb)} МБ. "
            "Отправьте изображение меньшего размера."
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
        return (
            "Не удалось получить фото из Telegram. "
            "Отправьте его ещё раз."
        )
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


async def handle_start_message(message: Message, *, bot: Bot) -> None:
    if not _trusted_private_message(message):
        return
    await bot.send_message(message.chat.id, NO_SOURCE_COPY)


async def handle_unsupported_message(message: Message, *, bot: Bot) -> None:
    if not _trusted_private_message(message):
        return
    await bot.send_message(message.chat.id, UNSUPPORTED_MESSAGE_COPY)


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
        "Выбрать цвет"
        if decision.became_active
        else "Выбрать цвет для активного фото"
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
) -> Router:
    """Build the three ordered private-chat handlers."""

    router = Router(name="car-wrap-private-ingress")

    @router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
    async def start_handler(message: Message, bot: Bot) -> None:
        await handle_start_message(message, bot=bot)

    @router.message(
        F.chat.type == ChatType.PRIVATE,
        F.photo | F.document,
    )
    async def media_handler(message: Message, bot: Bot) -> None:
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
