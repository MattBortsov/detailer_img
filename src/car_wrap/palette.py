"""Immutable server-owned color and surprise catalog."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from car_wrap.eval.models import EvaluationColor

PALETTE_VERSION: Final = "1"


class PaletteLookupError(ValueError):
    """Fixed-message rejection for a non-catalog intent."""

    def __init__(self) -> None:
        super().__init__("palette choice is invalid")


@dataclass(frozen=True, slots=True)
class PaletteChoice:
    """One audited color shared by evaluation and public display."""

    color_id: str
    prompt_name: str
    ui_name_ru: str
    display_hex: str


@dataclass(frozen=True, slots=True)
class SurpriseChoice:
    """Server-owned surprise intent without any client color value."""

    color_id: str
    ui_name_ru: str


PALETTE_CHOICES: Final[tuple[PaletteChoice, ...]] = (
    PaletteChoice(
        "pearl-white",
        "Pearl White",
        "Жемчужно-белый",
        "#F4F1E8",
    ),
    PaletteChoice("charcoal", "Charcoal", "Графитовый", "#343A40"),
    PaletteChoice("deep-blue", "Deep Blue", "Глубокий синий", "#123A66"),
    PaletteChoice("warm-red", "Warm Red", "Тёплый красный", "#B83232"),
    PaletteChoice(
        "forest-green",
        "Forest Green",
        "Лесной зелёный",
        "#275D38",
    ),
    PaletteChoice("copper", "Copper", "Медный", "#B66A3C"),
    PaletteChoice(
        "bright-yellow",
        "Bright Yellow",
        "Ярко-жёлтый",
        "#FFD21C",
    ),
    PaletteChoice("violet", "Violet", "Фиолетовый", "#6846A5"),
)
SURPRISE_CHOICE: Final = SurpriseChoice(
    color_id="surprise_me",
    ui_name_ru="Удиви меня",
)

_LOOKUP: Final[Mapping[str, PaletteChoice | SurpriseChoice]] = MappingProxyType(
    {
        **{choice.color_id: choice for choice in PALETTE_CHOICES},
        SURPRISE_CHOICE.color_id: SURPRISE_CHOICE,
    }
)
EVALUATION_COLORS: Final[Mapping[str, EvaluationColor]] = MappingProxyType(
    {
        choice.color_id: EvaluationColor(
            color_id=choice.color_id,
            display_name=choice.prompt_name,
            rgb_hex=choice.display_hex,
        )
        for choice in PALETTE_CHOICES
    }
)


def get_palette_choice(
    color_id: str,
) -> PaletteChoice | SurpriseChoice:
    """Return one closed catalog choice with no fallback."""

    try:
        return _LOOKUP[color_id]
    except (KeyError, TypeError):
        raise PaletteLookupError from None
