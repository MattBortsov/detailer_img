"""Typed server-owned built-in, custom and surprise generation intents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from car_wrap.db.models import CustomColorVersion
from car_wrap.palette import PaletteChoice, SurpriseChoice

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class BuiltInColorIntent:
    choice: PaletteChoice


@dataclass(frozen=True, slots=True)
class SurpriseIntent:
    choice: SurpriseChoice


@dataclass(frozen=True, slots=True)
class CustomColorIntent:
    color_id: UUID
    version_id: UUID
    version: int
    sha256: str
    object_key: str

    def __post_init__(self) -> None:
        if self.version <= 0 or not _SHA256.fullmatch(self.sha256):
            raise ValueError("invalid immutable custom color reference")


def custom_intent(version: CustomColorVersion) -> CustomColorIntent:
    return CustomColorIntent(
        color_id=version.custom_color_id,
        version_id=version.id,
        version=version.version,
        sha256=version.sha256,
        object_key=version.object_key,
    )
