"""Immutable server-owned color and surprise catalog."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final
from uuid import UUID

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

    def to_evaluation_color(self) -> EvaluationColor:
        return EvaluationColor(
            color_id=self.color_id,
            display_name=self.prompt_name,
            rgb_hex=self.display_hex,
        )


@dataclass(frozen=True, slots=True)
class SurpriseChoice:
    """Server-owned surprise intent without any client color value."""

    color_id: str
    ui_name_ru: str


@dataclass(frozen=True, slots=True)
class CustomSelection:
    color_id: UUID
    version: int


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
    {choice.color_id: choice.to_evaluation_color() for choice in PALETTE_CHOICES}
)


def get_palette_choice(
    color_id: str,
) -> PaletteChoice | SurpriseChoice:
    """Return one closed catalog choice with no fallback."""

    try:
        return _LOOKUP[color_id]
    except (KeyError, TypeError):
        raise PaletteLookupError from None


def custom_selection_id(color_id: UUID, version: int) -> str:
    if version <= 0:
        raise ValueError("version must be positive")
    return f"custom:{color_id}:v{version}"


def parse_custom_selection(value: str) -> CustomSelection:
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != "custom" or not parts[2].startswith("v"):
        raise PaletteLookupError
    try:
        color_id = UUID(parts[1])
        version = int(parts[2][1:])
    except (ValueError, TypeError):
        raise PaletteLookupError from None
    if version <= 0 or str(color_id) != parts[1].lower():
        raise PaletteLookupError
    return CustomSelection(color_id=color_id, version=version)
