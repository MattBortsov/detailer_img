"""Pure UUID-only relay contract tests."""

from __future__ import annotations

from uuid import uuid4

import pytest

from car_wrap.jobs.relay import canonical_job_id, job_hint


def test_job_hint_contains_only_canonical_uuid() -> None:
    job_id = uuid4()

    payload = job_hint(job_id)

    assert payload == str(job_id)
    assert canonical_job_id(payload) == job_id
    assert canonical_job_id(payload.encode("ascii")) == job_id


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "not-a-uuid",
        "A" * 36,
        "00000000000000000000000000000000",
        "00000000-0000-0000-0000-000000000000 extra",
    ],
)
def test_noncanonical_job_hints_are_rejected(payload: str) -> None:
    with pytest.raises(ValueError, match="canonical UUID"):
        canonical_job_id(payload)
