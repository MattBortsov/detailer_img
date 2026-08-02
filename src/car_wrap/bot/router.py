"""Private-chat Telegram intake and truthful active-source replies."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID, uuid4

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    Document,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from car_wrap.bot.delivery import MENU_CALLBACK_DATA
from car_wrap.bot.media import (
    MediaRejection,
    MediaRejectionCode,
    read_supported_media,
    read_supported_media_bytes,
)
from car_wrap.config import AppSettings
from car_wrap.palette import custom_selection_id
from car_wrap.services.active_source import (
    ActiveSourceDecision,
    set_active_source,
)

NO_SOURCE_COPY = (
    "Отправьте фото автомобиля или мотоцикла. Лучше всего подойдёт чёткий "
    "кадр, где хорошо видны окрашенные части."
)
UNSUPPORTED_MESSAGE_COPY = (
    "Нужна фотография автомобиля или мотоцикла. Отправьте фото или изображение файлом."
)
WINNING_SOURCE_COPY = "Теперь выберите цвет."
OLDER_SOURCE_COPY = "Фото принято, но для оклейки уже выбрано более новое фото."
MENU_COPY = "Отправьте новое фото или выберите другой цвет для текущего."
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


def replace_photo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=REPLACE_PHOTO_CANCEL_CALLBACK_DATA,
                )
            ]
        ]
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
    command: CommandObject | None = None,
    pending_uploads: CustomColorUploads | None = None,
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
                        text="Запустить генерацию с этим цветом",
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
        MENU_COPY,
        reply_markup=palette_keyboard(text="Выбрать цвет", settings=settings),
    )


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
        reply_markup=palette_keyboard(text="Выбрать цвет", settings=settings),
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
) -> Router:
    """Build the three ordered private-chat handlers."""

    router = Router(name="car-wrap-private-ingress")
    pending_custom_color_uploads: CustomColorUploads = {}

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
            command=command,
            pending_uploads=pending_custom_color_uploads,
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
