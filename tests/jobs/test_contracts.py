"""Pure durable-job contract tests."""

from __future__ import annotations

from uuid import uuid4

import pytest

from car_wrap.jobs.contracts import IntentKind, IntentSnapshot


def test_intent_snapshots_are_closed_and_exclusive() -> None:
    IntentSnapshot(
        kind=IntentKind.PALETTE,
        display_name="Графитовый",
        palette_color_id="charcoal",
    )
    IntentSnapshot(kind=IntentKind.SURPRISE, display_name="Удиви меня")
    IntentSnapshot(
        kind=IntentKind.CUSTOM,
        display_name="Bronze",
        custom_color_version_id=uuid4(),
        custom_color_sha256="a" * 64,
    )

    with pytest.raises(ValueError):
        IntentSnapshot(
            kind=IntentKind.PALETTE,
            display_name="Mixed",
            palette_color_id="charcoal",
            custom_color_version_id=uuid4(),
            custom_color_sha256="a" * 64,
        )


@pytest.mark.parametrize("digest", ["", "A" * 64, "a" * 63, "not-a-digest"])
def test_custom_digest_is_canonical_sha256(digest: str) -> None:
    with pytest.raises(ValueError):
        IntentSnapshot(
            kind=IntentKind.CUSTOM,
            display_name="Bronze",
            custom_color_version_id=uuid4(),
            custom_color_sha256=digest,
        )
