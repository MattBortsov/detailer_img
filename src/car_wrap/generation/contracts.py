"""Typed server-owned built-in, custom and surprise generation intents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from car_wrap.custom_colors.analysis import (
    ColorStructure,
    ReferenceProfile,
    SurfaceFinish,
)
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
    color_structure: str = ColorStructure.UNSPECIFIED.value
    finish: str = SurfaceFinish.UNSPECIFIED.value
    color_profile: dict[str, object] | None = None
    provider_reference_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.version <= 0 or not _SHA256.fullmatch(self.sha256):
            raise ValueError("invalid immutable custom color reference")
        if self.provider_reference_sha256 is not None and not _SHA256.fullmatch(
            self.provider_reference_sha256
        ):
            raise ValueError("invalid cleaned custom color reference")
        if self.color_profile is None:
            if (
                self.color_structure != ColorStructure.UNSPECIFIED.value
                or self.finish != SurfaceFinish.UNSPECIFIED.value
            ):
                raise ValueError("custom reference profile is missing")
            return
        profile = ReferenceProfile.from_dict(self.color_profile)
        if (
            profile.structure.value != self.color_structure
            or profile.finish.value != self.finish
            or self.provider_reference_sha256 is None
        ):
            raise ValueError("custom reference profile mismatch")


def custom_intent(
    version: CustomColorVersion,
    *,
    provider_reference_sha256: str | None = None,
) -> CustomColorIntent:
    profile = getattr(version, "color_profile", None)
    structure = getattr(version, "color_structure", None) or (
        ColorStructure.UNSPECIFIED.value
    )
    finish = getattr(version, "finish", None) or SurfaceFinish.UNSPECIFIED.value
    return CustomColorIntent(
        color_id=version.custom_color_id,
        version_id=version.id,
        version=version.version,
        sha256=version.sha256,
        object_key=version.object_key,
        color_structure=structure,
        finish=finish,
        color_profile=profile,
        provider_reference_sha256=provider_reference_sha256,
    )
