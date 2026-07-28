"""Immutable shared palette catalog."""

from __future__ import annotations

import pytest

from car_wrap.palette import (
    EVALUATION_COLORS,
    PALETTE_CHOICES,
    PALETTE_VERSION,
    SURPRISE_CHOICE,
    PaletteChoice,
    PaletteLookupError,
    SurpriseChoice,
    get_palette_choice,
)


def test_palette_order_values_and_russian_labels_are_exact() -> None:
    assert PALETTE_VERSION == "1"
    assert [
        (choice.color_id, choice.ui_name_ru, choice.display_hex)
        for choice in PALETTE_CHOICES
    ] == [
        ("pearl-white", "Жемчужно-белый", "#F4F1E8"),
        ("charcoal", "Графитовый", "#343A40"),
        ("deep-blue", "Глубокий синий", "#123A66"),
        ("warm-red", "Тёплый красный", "#B83232"),
        ("forest-green", "Лесной зелёный", "#275D38"),
        ("copper", "Медный", "#B66A3C"),
        ("bright-yellow", "Ярко-жёлтый", "#FFD21C"),
        ("violet", "Фиолетовый", "#6846A5"),
    ]
    assert all(isinstance(choice, PaletteChoice) for choice in PALETTE_CHOICES)


def test_surprise_is_separate_last_intent_without_color_or_prompt() -> None:
    assert SURPRISE_CHOICE == SurpriseChoice(
        color_id="surprise_me",
        ui_name_ru="Удиви меня",
    )
    assert not hasattr(SURPRISE_CHOICE, "display_hex")
    assert not hasattr(SURPRISE_CHOICE, "prompt_name")


def test_closed_lookup_rejects_unknown_or_malformed_ids() -> None:
    assert get_palette_choice("pearl-white") is PALETTE_CHOICES[0]
    assert get_palette_choice("surprise_me") is SURPRISE_CHOICE
    for value in ("", "PEARL-WHITE", "unknown", "../pearl-white", " red "):
        with pytest.raises(PaletteLookupError):
            get_palette_choice(value)


def test_catalog_and_evaluation_mapping_are_immutable_and_compatible() -> None:
    with pytest.raises(TypeError):
        EVALUATION_COLORS["new"] = EVALUATION_COLORS["charcoal"]  # type: ignore[index]

    assert list(EVALUATION_COLORS) == [choice.color_id for choice in PALETTE_CHOICES]
    for choice in PALETTE_CHOICES:
        evaluation = EVALUATION_COLORS[choice.color_id]
        assert evaluation.color_id == choice.color_id
        assert evaluation.display_name == choice.prompt_name
        assert evaluation.rgb_hex == choice.display_hex
