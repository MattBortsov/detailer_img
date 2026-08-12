from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram.enums import ChatType

from car_wrap.billing.repository import BillingRepository
from car_wrap.bot.router import (
    BILLING_BACK_CALLBACK_DATA,
    BILLING_INTRO_SAVED_PREFIX,
    handle_billing_product,
    handle_intro_saved_card,
    intro_card_keyboard,
    paywall_keyboard,
)


def test_first_intro_button_opens_checkout_directly() -> None:
    keyboard = paywall_keyboard(
        intro_available=True,
        intro_checkout_url="https://seo-smith.ru/pay/AbCdEfGhIjKlMnOpQrStUv",
    )

    button = keyboard.inline_keyboard[0][0]
    assert button.text == "💳 1 генерация — 25 ₽"
    assert button.url == "https://seo-smith.ru/pay/AbCdEfGhIjKlMnOpQrStUv"
    assert button.callback_data is None


def test_saved_card_choice_includes_other_card_and_back() -> None:
    source_id = uuid4()
    keyboard = intro_card_keyboard(
        source_id=source_id,
        other_checkout_url="https://seo-smith.ru/pay/AbCdEfGhIjKlMnOpQrStUv",
    )

    saved, other, back = (row[0] for row in keyboard.inline_keyboard)
    assert saved.callback_data == f"{BILLING_INTRO_SAVED_PREFIX}{source_id}"
    assert other.url == "https://seo-smith.ru/pay/AbCdEfGhIjKlMnOpQrStUv"
    assert back.callback_data == BILLING_BACK_CALLBACK_DATA


@pytest.mark.asyncio
async def test_intro_offer_never_expires() -> None:
    session = AsyncMock()

    assert await BillingRepository().intro_offer_available(session, user_id=123)
    session.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_intro_sequence_continues_after_third_purchase() -> None:
    session = AsyncMock()
    session.scalar.return_value = 3

    assert await BillingRepository().next_intro_number(session, user_id=123) == 4


@pytest.mark.asyncio
async def test_existing_source_opens_card_choice_without_charging() -> None:
    callback = SimpleNamespace(
        id="callback-1",
        data="billing:product:intro_25",
        from_user=SimpleNamespace(id=123),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=123, type=ChatType.PRIVATE),
        ),
    )
    bot = AsyncMock()
    source_id = uuid4()
    payment_service = SimpleNamespace(
        production_available=lambda: True,
        active_intro_recurring_source=AsyncMock(
            return_value=SimpleNamespace(id=source_id)
        ),
        start_checkout=AsyncMock(
            return_value=(
                SimpleNamespace(),
                "https://seo-smith.ru/pay/AbCdEfGhIjKlMnOpQrStUv",
            )
        ),
        start_intro_recurring_charge=AsyncMock(),
    )

    await handle_billing_product(
        callback,
        bot=bot,
        payment_service=payment_service,
    )

    payment_service.start_intro_recurring_charge.assert_not_awaited()
    sent_keyboard = bot.send_message.await_args.kwargs["reply_markup"]
    assert sent_keyboard.inline_keyboard[0][0].callback_data == (
        f"{BILLING_INTRO_SAVED_PREFIX}{source_id}"
    )


@pytest.mark.asyncio
async def test_saved_card_click_starts_charge_immediately() -> None:
    source_id = uuid4()
    callback = SimpleNamespace(
        id="callback-2",
        data=f"{BILLING_INTRO_SAVED_PREFIX}{source_id}",
        from_user=SimpleNamespace(id=123),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=123, type=ChatType.PRIVATE),
        ),
    )
    bot = AsyncMock()
    payment_service = SimpleNamespace(
        start_intro_recurring_charge=AsyncMock(return_value=SimpleNamespace()),
    )

    await handle_intro_saved_card(
        callback,
        bot=bot,
        payment_service=payment_service,
    )

    payment_service.start_intro_recurring_charge.assert_awaited_once_with(
        user_id=123,
        source_id=source_id,
    )
    assert "Производим оплату" in bot.send_message.await_args.args[1]
