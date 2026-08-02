"""Bounded deterministic color extraction for custom wrap references."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from statistics import median

from PIL import Image, ImageCms, ImageFilter, ImageStat, UnidentifiedImageError

from car_wrap.custom_colors.moderation import ModerationResult, NormalizedRegion

ANALYSIS_REVISION = "reference-v1"
_WORK_LONG_EDGE = 512
_OUTPUT_SIDE = 512
_MIN_LOCALIZATION_CONFIDENCE = 50


class ReferenceAnalysisError(ValueError):
    """The reference does not contain enough trustworthy material pixels."""


class ColorStructure(StrEnum):
    UNSPECIFIED = "unspecified"
    SOLID = "solid"
    MULTICOLOR = "multicolor"


class SurfaceFinish(StrEnum):
    UNSPECIFIED = "unspecified"
    MATTE = "matte"
    SATIN = "satin"


@dataclass(frozen=True, slots=True)
class ColorCluster:
    rgb_hex: str
    lab: tuple[float, float, float]
    weight: float
    sample_box: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class ReferenceProfile:
    structure: ColorStructure
    finish: SurfaceFinish
    confidence: int
    palette: tuple[ColorCluster, ...]

    @property
    def base_rgb_hex(self) -> str | None:
        return (
            self.palette[0].rgb_hex if self.structure is ColorStructure.SOLID else None
        )

    @property
    def base_lab(self) -> tuple[float, float, float] | None:
        return self.palette[0].lab if self.structure is ColorStructure.SOLID else None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "revision": ANALYSIS_REVISION,
            "structure": self.structure.value,
            "finish": self.finish.value,
            "confidence": self.confidence,
            "palette": [
                {
                    "rgb_hex": cluster.rgb_hex,
                    "lab": [round(value, 2) for value in cluster.lab],
                    "weight": round(cluster.weight, 4),
                    "sample_box": list(cluster.sample_box),
                }
                for cluster in self.palette
            ],
        }
        if self.base_rgb_hex is not None and self.base_lab is not None:
            result["base_rgb_hex"] = self.base_rgb_hex
            result["base_lab"] = [round(value, 2) for value in self.base_lab]
        return result

    @classmethod
    def from_dict(cls, value: object) -> ReferenceProfile:
        if not isinstance(value, dict) or value.get("revision") != ANALYSIS_REVISION:
            raise ValueError("invalid reference profile revision")
        try:
            structure = ColorStructure(value["structure"])
            finish = SurfaceFinish(value["finish"])
            confidence = int(value["confidence"])
            raw_palette = value["palette"]
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid reference profile") from None
        if (
            structure is ColorStructure.UNSPECIFIED
            or finish is SurfaceFinish.UNSPECIFIED
            or not 0 <= confidence <= 100
            or not isinstance(raw_palette, list)
            or not 1 <= len(raw_palette) <= 5
        ):
            raise ValueError("invalid reference profile")
        palette: list[ColorCluster] = []
        for raw in raw_palette:
            if not isinstance(raw, dict):
                raise ValueError("invalid reference palette")
            rgb_hex = raw.get("rgb_hex")
            lab = raw.get("lab")
            weight = raw.get("weight")
            sample_box = raw.get("sample_box")
            if (
                not isinstance(rgb_hex, str)
                or len(rgb_hex) != 7
                or not rgb_hex.startswith("#")
                or any(character not in "0123456789ABCDEF" for character in rgb_hex[1:])
                or not isinstance(lab, list)
                or len(lab) != 3
                or not isinstance(sample_box, list)
                or len(sample_box) != 4
                or not isinstance(weight, (int, float))
                or isinstance(weight, bool)
            ):
                raise ValueError("invalid reference palette")
            try:
                lab_tuple = tuple(float(item) for item in lab)
                box_tuple = tuple(int(item) for item in sample_box)
                numeric_weight = float(weight)
            except (TypeError, ValueError):
                raise ValueError("invalid reference palette") from None
            if (
                len(lab_tuple) != 3
                or len(box_tuple) != 4
                or not 0 < numeric_weight <= 1
                or not _normalized_box_valid(box_tuple)
            ):
                raise ValueError("invalid reference palette")
            palette.append(
                ColorCluster(
                    rgb_hex,
                    (lab_tuple[0], lab_tuple[1], lab_tuple[2]),
                    numeric_weight,
                    (box_tuple[0], box_tuple[1], box_tuple[2], box_tuple[3]),
                )
            )
        expected = 1 if structure is ColorStructure.SOLID else range(2, 6)
        if (isinstance(expected, int) and len(palette) != expected) or (
            not isinstance(expected, int) and len(palette) not in expected
        ):
            raise ValueError("profile palette does not match structure")
        return cls(structure, finish, confidence, tuple(palette))


@dataclass(frozen=True, slots=True)
class _Tile:
    rgb: tuple[int, int, int]
    lab: tuple[float, float, float]
    box: tuple[int, int, int, int]
    quality: float


@dataclass(slots=True)
class _WorkingCluster:
    tiles: list[_Tile]

    @property
    def center(self) -> tuple[float, float, float]:
        return tuple(
            float(median(tile.lab[index] for tile in self.tiles)) for index in range(3)
        )  # type: ignore[return-value]


def _normalized_box_valid(box: tuple[int, ...]) -> bool:
    if len(box) != 4:
        return False
    x, y, width, height = box
    return (
        0 <= x <= 999
        and 0 <= y <= 999
        and 1 <= width <= 1000
        and 1 <= height <= 1000
        and x + width <= 1000
        and y + height <= 1000
    )


def _scale_region(
    region: NormalizedRegion,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    left = round(region.x * width / 1000)
    top = round(region.y * height / 1000)
    right = round((region.x + region.width) * width / 1000)
    bottom = round((region.y + region.height) * height / 1000)
    return left, top, max(left + 1, right), max(top + 1, bottom)


def _normalize_box(
    box: tuple[int, int, int, int],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    return (
        max(0, min(999, round(left * 1000 / width))),
        max(0, min(999, round(top * 1000 / height))),
        max(1, min(1000, round((right - left) * 1000 / width))),
        max(1, min(1000, round((bottom - top) * 1000 / height))),
    )


def _intersection_ratio(
    box: tuple[int, int, int, int],
    excluded: tuple[int, int, int, int],
) -> float:
    left = max(box[0], excluded[0])
    top = max(box[1], excluded[1])
    right = min(box[2], excluded[2])
    bottom = min(box[3], excluded[3])
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    area = max(1, (box[2] - box[0]) * (box[3] - box[1]))
    return intersection / area


def _lab_distance(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    return math.sqrt(
        sum((left - right) ** 2 for left, right in zip(first, second, strict=True))
    )


def _lab_values(encoded: Sequence[float]) -> tuple[float, float, float]:
    return encoded[0] * 100 / 255, encoded[1] - 128, encoded[2] - 128


def _rgb_hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{channel:02X}" for channel in rgb)


def _hex_rgb(value: str) -> tuple[int, int, int]:
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def _mix_rgb(
    left: tuple[int, int, int],
    right: tuple[int, int, int],
    fraction: float,
) -> tuple[int, int, int]:
    return (
        round(left[0] * (1 - fraction) + right[0] * fraction),
        round(left[1] * (1 - fraction) + right[1] * fraction),
        round(left[2] * (1 - fraction) + right[2] * fraction),
    )


def _prepare_images(data: bytes) -> tuple[Image.Image, Image.Image, Image.Image]:
    try:
        with Image.open(BytesIO(data)) as source:
            source.load()
            rgb = source.convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise ReferenceAnalysisError("reference cannot be decoded") from None
    longest = max(rgb.size)
    if longest > _WORK_LONG_EDGE:
        scale = _WORK_LONG_EDGE / longest
        rgb = rgb.resize(
            (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))),
            Image.Resampling.LANCZOS,
        )
    converted = ImageCms.profileToProfile(
        rgb,
        ImageCms.createProfile("sRGB"),
        ImageCms.createProfile("LAB"),
        outputMode="LAB",
    )
    if converted is None:
        raise ReferenceAnalysisError("reference color conversion failed")
    edges = rgb.convert("L").filter(ImageFilter.FIND_EDGES)
    return rgb, converted, edges


def _candidate_tiles(
    data: bytes,
    moderation: ModerationResult,
    structure: ColorStructure,
) -> tuple[Image.Image, list[_Tile]]:
    rgb, lab, edges = _prepare_images(data)
    width, height = rgb.size
    if (
        moderation.localization_confidence >= _MIN_LOCALIZATION_CONFIDENCE
        and moderation.material_regions
    ):
        candidates = [
            _scale_region(region, width, height)
            for region in moderation.material_regions
        ]
    else:
        candidates = [
            (
                round(width * 0.1),
                round(height * 0.1),
                round(width * 0.9),
                round(height * 0.9),
            )
        ]
    excluded = [
        _scale_region(region, width, height) for region in moderation.excluded_regions
    ]
    tile_side = max(16, min(64, min(width, height) // 6))
    stride = max(8, tile_side // 2)
    tiles: list[_Tile] = []
    for candidate in candidates:
        left, top, right, bottom = candidate
        if right - left < tile_side or bottom - top < tile_side:
            continue
        for y in range(top, bottom - tile_side + 1, stride):
            for x in range(left, right - tile_side + 1, stride):
                box = (x, y, x + tile_side, y + tile_side)
                if any(_intersection_ratio(box, item) >= 0.2 for item in excluded):
                    continue
                edge_mean = ImageStat.Stat(edges.crop(box)).mean[0]
                lab_stat = ImageStat.Stat(lab.crop(box))
                lab_median = _lab_values(lab_stat.median)
                dispersion = sum(lab_stat.stddev) / 3
                if (
                    lab_median[0] < 5
                    or lab_median[0] > 96
                    or edge_mean > 48
                    or dispersion > (38 if structure is ColorStructure.SOLID else 58)
                ):
                    continue
                rgb_median = tuple(
                    int(value) for value in ImageStat.Stat(rgb.crop(box)).median
                )
                tiles.append(
                    _Tile(
                        (rgb_median[0], rgb_median[1], rgb_median[2]),
                        lab_median,
                        box,
                        max(0.0, 100 - edge_mean - dispersion),
                    )
                )
    if len(tiles) < 3:
        raise ReferenceAnalysisError("not enough clean material area")
    return rgb, tiles


def _cluster_tiles(tiles: list[_Tile], *, threshold: float) -> list[_WorkingCluster]:
    clusters: list[_WorkingCluster] = []
    for tile in sorted(tiles, key=lambda item: item.quality, reverse=True):
        closest = min(
            clusters,
            key=lambda cluster: _lab_distance(tile.lab, cluster.center),
            default=None,
        )
        if closest is None or _lab_distance(tile.lab, closest.center) > threshold:
            clusters.append(_WorkingCluster([tile]))
        else:
            closest.tiles.append(tile)
    return clusters


def _cluster_value(
    cluster: _WorkingCluster,
    *,
    weight: float,
    width: int,
    height: int,
) -> ColorCluster:
    rgb = tuple(
        int(median(tile.rgb[index] for tile in cluster.tiles)) for index in range(3)
    )
    lab = cluster.center
    representative = min(cluster.tiles, key=lambda tile: _lab_distance(tile.lab, lab))
    return ColorCluster(
        _rgb_hex((rgb[0], rgb[1], rgb[2])),
        lab,
        weight,
        _normalize_box(representative.box, width, height),
    )


def analyze_reference(
    data: bytes,
    structure: ColorStructure | str,
    finish: SurfaceFinish | str,
    moderation: ModerationResult,
) -> ReferenceProfile:
    """Extract one bounded profile without trusting text or scene-wide averages."""

    try:
        normalized_structure = ColorStructure(structure)
        normalized_finish = SurfaceFinish(finish)
    except ValueError:
        raise ReferenceAnalysisError("unsupported reference metadata") from None
    if (
        normalized_structure is ColorStructure.UNSPECIFIED
        or normalized_finish is SurfaceFinish.UNSPECIFIED
    ):
        raise ReferenceAnalysisError("reference metadata is required")
    image, tiles = _candidate_tiles(data, moderation, normalized_structure)
    clusters = _cluster_tiles(
        tiles,
        threshold=22 if normalized_structure is ColorStructure.SOLID else 14,
    )
    clusters.sort(key=lambda item: len(item.tiles), reverse=True)
    palette: tuple[ColorCluster, ...]
    if normalized_structure is ColorStructure.SOLID:
        primary = clusters[0]
        support = len(primary.tiles) / len(tiles)
        if len(primary.tiles) < 3 or support < 0.4:
            raise ReferenceAnalysisError("solid material color is uncertain")
        confidence = max(1, min(100, round(support * 100)))
        palette = (
            _cluster_value(
                primary,
                weight=1.0,
                width=image.width,
                height=image.height,
            ),
        )
    else:
        minimum_support = max(2, math.ceil(len(tiles) * 0.08))
        supported = [
            cluster for cluster in clusters if len(cluster.tiles) >= minimum_support
        ]
        supported = supported[:5]
        if len(supported) < 2:
            raise ReferenceAnalysisError("multicolor material palette is uncertain")
        supported.sort(
            key=lambda cluster: min(tile.box[0] + tile.box[1] for tile in cluster.tiles)
        )
        total = sum(len(cluster.tiles) for cluster in supported)
        palette = tuple(
            _cluster_value(
                cluster,
                weight=len(cluster.tiles) / total,
                width=image.width,
                height=image.height,
            )
            for cluster in supported
        )
        confidence = max(1, min(100, round(total / len(tiles) * 100)))
    return ReferenceProfile(
        normalized_structure,
        normalized_finish,
        confidence,
        palette,
    )


def _source_box(
    normalized: tuple[int, int, int, int],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    x, y, box_width, box_height = normalized
    left = round(x * width / 1000)
    top = round(y * height / 1000)
    right = round((x + box_width) * width / 1000)
    bottom = round((y + box_height) * height / 1000)
    return left, top, max(left + 1, right), max(top + 1, bottom)


def build_clean_reference(data: bytes, profile: ReferenceProfile | object) -> bytes:
    """Reconstruct one metadata-free provider reference entirely in memory."""

    resolved = (
        profile
        if isinstance(profile, ReferenceProfile)
        else ReferenceProfile.from_dict(profile)
    )
    try:
        with Image.open(BytesIO(data)) as source:
            source.load()
            image = source.convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise ReferenceAnalysisError("reference cannot be reconstructed") from None
    if resolved.structure is ColorStructure.SOLID:
        cluster = resolved.palette[0]
        crop = image.crop(_source_box(cluster.sample_box, image.width, image.height))
        texture = crop.resize(
            (_OUTPUT_SIDE, _OUTPUT_SIDE),
            Image.Resampling.BICUBIC,
        )
        base_rgb = tuple(
            int(cluster.rgb_hex[index : index + 2], 16) for index in (1, 3, 5)
        )
        reference = Image.blend(
            texture,
            Image.new("RGB", texture.size, base_rgb),
            0.72,
        )
    else:
        colors = [_hex_rgb(cluster.rgb_hex) for cluster in resolved.palette]
        row: list[tuple[int, int, int]] = []
        for x in range(_OUTPUT_SIDE):
            position = x / (_OUTPUT_SIDE - 1) * (len(colors) - 1)
            left_index = min(len(colors) - 1, int(position))
            right_index = min(len(colors) - 1, left_index + 1)
            fraction = position - left_index
            row.append(_mix_rgb(colors[left_index], colors[right_index], fraction))
        strip = Image.new("RGB", (_OUTPUT_SIDE, 1))
        strip.putdata(row)
        reference = strip.resize((_OUTPUT_SIDE, _OUTPUT_SIDE))
    output = BytesIO()
    reference.save(output, format="PNG", optimize=True)
    return output.getvalue()
