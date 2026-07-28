"""Creation orchestration and compensation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from car_wrap.custom_colors.media import CanonicalImage
from car_wrap.custom_colors.moderation import (
    ModerationDisposition,
    ModerationResult,
)
from car_wrap.custom_colors.service import CustomColorService
from car_wrap.custom_colors.storage import StoredObject


class Storage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def put(self, data: bytes) -> StoredObject:
        return StoredObject(
            key="aa/bb/" + "c" * 32 + ".png", sha256="d" * 64, byte_size=len(data)
        )

    def delete(self, key: str) -> None:
        self.deleted.append(key)


@dataclass
class Color:
    id: UUID
    status: str = "pending"


class Repository:
    def __init__(self, *, fail_create: bool = False) -> None:
        self.fail_create = fail_create
        self.applied: list[tuple[UUID, str, str]] = []
        self.color = Color(uuid4())

    async def create(self, session: object, **kwargs: object) -> Color:
        if self.fail_create:
            raise RuntimeError("database failed")
        return self.color

    async def apply_moderation(
        self,
        session: object,
        *,
        color_id: UUID,
        idempotency_key: str,
        result: ModerationResult,
        provider_model: str,
    ) -> Color:
        marker = (color_id, idempotency_key, result.disposition.value)
        if marker not in self.applied:
            self.applied.append(marker)
        self.color.status = result.disposition.value
        return self.color


class Session:
    def __init__(self, *, fail_commit: bool = False) -> None:
        self.fail_commit = fail_commit
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_success_persists_canonical_bytes_and_applies_moderation() -> None:
    storage = Storage()
    repository = Repository()
    session = Session()

    async def moderate(data: bytes) -> ModerationResult:
        assert data == b"png"
        return ModerationResult(
            ModerationDisposition.APPROVED,
            "approved",
            98,
            97,
        )

    service = CustomColorService(
        storage=storage,
        repository=repository,
        normalize=lambda data, mime: CanonicalImage(
            b"png", "image/png", 80, 60, "d" * 64
        ),
        moderate=moderate,
        moderation_model="vision-model",
    )
    color = await service.create(
        session,
        owner_id=42,
        display_name=" Bronze ",
        upload=b"source",
        declared_mime="image/png",
        idempotency_key="request-1",
    )

    assert color.status == "approved"
    assert session.commits == 2
    assert not storage.deleted
    assert len(repository.applied) == 1


@pytest.mark.asyncio
async def test_database_failure_compensates_stored_object() -> None:
    storage = Storage()
    session = Session()
    service = CustomColorService(
        storage=storage,
        repository=Repository(fail_create=True),
        normalize=lambda data, mime: CanonicalImage(
            b"png", "image/png", 80, 60, "d" * 64
        ),
        moderate=lambda data: None,
        moderation_model="vision-model",
    )

    with pytest.raises(RuntimeError):
        await service.create(
            session,
            owner_id=42,
            display_name="Bronze",
            upload=b"source",
            declared_mime="image/png",
            idempotency_key="request-1",
        )

    assert storage.deleted == ["aa/bb/" + "c" * 32 + ".png"]
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_commit_failure_compensates_stored_object() -> None:
    storage = Storage()
    session = Session(fail_commit=True)
    service = CustomColorService(
        storage=storage,
        repository=Repository(),
        normalize=lambda data, mime: CanonicalImage(
            b"png", "image/png", 80, 60, "d" * 64
        ),
        moderate=lambda data: None,
        moderation_model="vision-model",
    )

    with pytest.raises(RuntimeError, match="commit"):
        await service.create(
            session,
            owner_id=42,
            display_name="Bronze",
            upload=b"source",
            declared_mime="image/png",
            idempotency_key="request-1",
        )
    assert len(storage.deleted) == 1
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_moderation_exception_fails_closed_to_review() -> None:
    repository = Repository()

    async def unavailable(data: bytes) -> ModerationResult:
        raise RuntimeError("provider offline")

    service = CustomColorService(
        storage=Storage(),
        repository=repository,
        normalize=lambda data, mime: CanonicalImage(
            b"png", "image/png", 80, 60, "d" * 64
        ),
        moderate=unavailable,
        moderation_model="vision-model",
    )
    color = await service.create(
        Session(),
        owner_id=42,
        display_name="Bronze",
        upload=b"source",
        declared_mime="image/png",
        idempotency_key="request-1",
    )
    assert color.status == "needs_review"
    assert repository.applied[0][2] == "needs_review"
