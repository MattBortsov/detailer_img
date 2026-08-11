"""Locked Russian bot billing-menu contracts."""

from car_wrap.bot.router import (
    BILLING_CANCEL_CALLBACK_DATA,
    monthly_keyboard,
    package_keyboard,
    paywall_keyboard,
    subscription_cancel_keyboard,
)


def test_paywall_keeps_locked_intro_row_geometry() -> None:
    keyboard = paywall_keyboard(intro_available=True).inline_keyboard
    assert [[button.text for button in row] for row in keyboard] == [
        ["1 генерация — 25 ₽"],
        ["Пакеты", "Месяц"],
    ]


def test_paywall_hides_intro_after_three_deliveries() -> None:
    keyboard = paywall_keyboard(intro_available=False).inline_keyboard
    assert [[button.text for button in row] for row in keyboard] == [
        ["Пакеты", "Месяц"]
    ]


def test_catalogs_are_category_scoped_and_have_back() -> None:
    package_labels = [row[0].text for row in package_keyboard().inline_keyboard]
    monthly_labels = [row[0].text for row in monthly_keyboard().inline_keyboard]
    assert package_labels == [
        "5 генераций — 149 ₽",
        "15 генераций — 349 ₽",
        "40 генераций — 749 ₽",
        "Назад",
    ]
    assert monthly_labels == [
        "Plus: 30 генераций — 499 ₽/месяц",
        "Studio: 100 генераций — 1 499 ₽/месяц",
        "Ultima — Узнать цену",
        "Назад",
    ]


def test_active_subscription_has_a_rendered_cancellation_action() -> None:
    button = subscription_cancel_keyboard().inline_keyboard[0][0]
    assert button.text == "Отключить автопродление"
    assert button.callback_data == BILLING_CANCEL_CALLBACK_DATA
