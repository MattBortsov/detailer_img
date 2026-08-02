"""Deterministic custom-reference color extraction contracts."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, ImageDraw, ImageStat

from car_wrap.custom_colors.analysis import (
    ANALYSIS_REVISION,
    ColorStructure,
    ReferenceAnalysisError,
    ReferenceProfile,
    SurfaceFinish,
    analyze_reference,
    build_clean_reference,
)
from car_wrap.custom_colors.moderation import (
    ModerationDisposition,
    ModerationResult,
    NormalizedRegion,
)


def png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def moderation(
    *,
    material: tuple[NormalizedRegion, ...] = (NormalizedRegion(100, 100, 800, 800),),
    excluded: tuple[NormalizedRegion, ...] = (),
) -> ModerationResult:
    return ModerationResult(
        ModerationDisposition.APPROVED,
        "approved",
        98,
        97,
        material,
        excluded,
        95,
    )


def test_solid_ignores_background_text_highlight_and_shadow() -> None:
    image = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 30, 360, 270), fill=(205, 42, 31))
    draw.rectangle((40, 30, 360, 60), fill=(248, 116, 100))
    draw.rectangle((40, 240, 360, 270), fill=(82, 18, 15))
    draw.text((150, 130), "ORANGE TPU-060", fill="black")
    profile = analyze_reference(
        png(image),
        ColorStructure.SOLID,
        SurfaceFinish.MATTE,
        moderation(
            material=(NormalizedRegion(100, 100, 800, 800),),
            excluded=(NormalizedRegion(350, 390, 390, 160),),
        ),
    )

    assert profile.structure is ColorStructure.SOLID
    assert profile.finish is SurfaceFinish.MATTE
    red, green, blue = (
        int(profile.base_rgb_hex[index : index + 2], 16) for index in (1, 3, 5)
    )
    assert red > 170
    assert green < 80
    assert blue < 70
    assert profile.to_dict()["revision"] == ANALYSIS_REVISION

    cleaned = build_clean_reference(png(image), profile)
    with Image.open(BytesIO(cleaned)) as result:
        assert result.size == (512, 512)
        assert result.format == "PNG"
        median_rgb = ImageStat.Stat(result).median
    assert median_rgb[0] > 170
    assert median_rgb[1] < 90
    assert median_rgb[2] < 80


def test_solid_liquid_metal_bands_are_lighting_not_separate_colors() -> None:
    image = Image.new("RGB", (420, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((42, 30, 378, 78), fill=(255, 190, 180))
    draw.rectangle((42, 78, 378, 126), fill=(245, 115, 95))
    draw.rectangle((42, 126, 378, 174), fill=(205, 42, 31))
    draw.rectangle((42, 174, 378, 222), fill=(130, 15, 15))
    draw.rectangle((42, 222, 378, 270), fill=(65, 3, 3))
    draw.text((205, 140), "LIQUID METAL TPU-112B", fill="white")

    profile = analyze_reference(
        png(image),
        ColorStructure.SOLID,
        SurfaceFinish.MATTE,
        moderation(
            material=(NormalizedRegion(100, 100, 800, 800),),
            excluded=(NormalizedRegion(450, 430, 430, 150),),
        ),
    )

    assert profile.structure is ColorStructure.SOLID
    assert profile.base_rgb_hex is not None
    red, green, blue = (
        int(profile.base_rgb_hex[index : index + 2], 16) for index in (1, 3, 5)
    )
    assert red > green * 2
    assert red > blue * 2


def test_multicolor_preserves_multiple_supported_hues() -> None:
    image = Image.new("RGB", (360, 240), (80, 89, 108))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 120, 240), fill=(88, 73, 122))
    draw.rectangle((120, 0, 240, 240), fill=(122, 72, 194))
    draw.rectangle((240, 0, 360, 240), fill=(198, 82, 229))
    profile = analyze_reference(
        png(image),
        ColorStructure.MULTICOLOR,
        SurfaceFinish.SATIN,
        moderation(material=(NormalizedRegion(0, 0, 1000, 1000),)),
    )

    assert 2 <= len(profile.palette) <= 5
    assert profile.structure is ColorStructure.MULTICOLOR
    assert sum(cluster.weight for cluster in profile.palette) == pytest.approx(1)
    restored = ReferenceProfile.from_dict(profile.to_dict())
    assert restored.structure is ColorStructure.MULTICOLOR
    assert len(restored.palette) == len(profile.palette)

    with Image.open(BytesIO(build_clean_reference(png(image), profile))) as cleaned:
        assert cleaned.size == (512, 512)
        assert cleaned.getpixel((0, 256)) != cleaned.getpixel((511, 256))


def test_uncertain_or_mismatched_reference_fails_instead_of_guessing() -> None:
    image = Image.new("RGB", (160, 160), "white")
    draw = ImageDraw.Draw(image)
    for offset in range(0, 160, 8):
        draw.line((0, offset, 159, 159 - offset), fill="black", width=4)

    with pytest.raises(ReferenceAnalysisError):
        analyze_reference(
            png(image),
            ColorStructure.SOLID,
            SurfaceFinish.MATTE,
            moderation(material=(NormalizedRegion(0, 0, 1000, 1000),)),
        )

    solid = Image.new("RGB", (180, 180), (40, 100, 180))
    with pytest.raises(ReferenceAnalysisError, match="multicolor"):
        analyze_reference(
            png(solid),
            ColorStructure.MULTICOLOR,
            SurfaceFinish.SATIN,
            moderation(material=(NormalizedRegion(0, 0, 1000, 1000),)),
        )


def test_profile_parser_rejects_untrusted_metadata() -> None:
    with pytest.raises(ValueError):
        ReferenceProfile.from_dict(
            {
                "revision": ANALYSIS_REVISION,
                "structure": "solid",
                "finish": "matte",
                "confidence": 101,
                "palette": [],
            }
        )
