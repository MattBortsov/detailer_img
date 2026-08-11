"""Pure allowance-selection rules for paid generation acceptance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from car_wrap.billing.allowances import (
    AllowanceCandidate,
    AllowanceUnavailable,
    select_candidate,
)
from car_wrap.billing.contracts import AllowanceKind

NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


def candidate(
    kind: AllowanceKind,
    *,
    available: int = 1,
    expires_at: datetime | None = None,
) -> AllowanceCandidate:
    return AllowanceCandidate(kind=kind, available=available, expires_at=expires_at)


def test_selects_free_then_intro_then_package_bonus_then_monthly() -> None:
    assert (
        select_candidate(
            [
                candidate(AllowanceKind.MONTHLY),
                candidate(AllowanceKind.BONUS),
                candidate(AllowanceKind.PACKAGE),
                candidate(AllowanceKind.INTRO),
                candidate(AllowanceKind.FREE),
            ],
            now=NOW,
        ).kind
        is AllowanceKind.FREE
    )
    assert (
        select_candidate(
            [
                candidate(AllowanceKind.MONTHLY),
                candidate(AllowanceKind.BONUS),
                candidate(AllowanceKind.PACKAGE),
                candidate(AllowanceKind.INTRO),
            ],
            now=NOW,
        ).kind
        is AllowanceKind.INTRO
    )
    assert (
        select_candidate(
            [
                candidate(AllowanceKind.MONTHLY),
                candidate(AllowanceKind.BONUS),
                candidate(AllowanceKind.PACKAGE),
            ],
            now=NOW,
        ).kind
        is AllowanceKind.PACKAGE
    )


def test_expired_monthly_allowance_is_never_selected() -> None:
    selected = select_candidate(
        [
            candidate(AllowanceKind.MONTHLY, expires_at=NOW - timedelta(seconds=1)),
            candidate(AllowanceKind.BONUS),
        ],
        now=NOW,
    )
    assert selected.kind is AllowanceKind.BONUS


def test_no_available_allowance_has_stable_paywall_error() -> None:
    with pytest.raises(AllowanceUnavailable, match="allowance_required"):
        select_candidate(
            [candidate(AllowanceKind.FREE, available=0)],
            now=NOW,
        )
