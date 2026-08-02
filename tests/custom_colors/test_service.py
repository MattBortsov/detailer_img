"""Creation orchestration and compensation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from car_wrap.custom_colors.analysis import (
    ColorCluster,
    ColorStructure,
    ReferenceProfile,
    SurfaceFinish,
)
from car_wrap.custom_colors.media import CanonicalImage
from car_wrap.custom_colors.moderation import (
    ModerationDisposition,
    ModerationResult,
)
from car_wrap.custom_colors.repository import VersionInput
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
    display_name: str = "Без названия"


class Repository:
    def __init__(self, *, fail_create: bool = False) -> None:
        self.fail_create = fail_create
        self.applied: list[tuple[UUID, str, str]] = []
        self.created: list[VersionInput] = []
        self.profiles: list[dict[str, object]] = []
        self.renamed: list[str] = []
        self.color = Color(uuid4())

    async def create(self, session: object, **kwargs: object) -> Color:
        if self.fail_create:
            raise RuntimeError("database failed")
        version = kwargs["version"]
        assert isinstance(version, VersionInput)
        self.created.append(version)
        return self.color

    async def apply_analysis(
        self,
        session: object,
        *,
        color_id: UUID,
        analysis_revision: str,
        color_profile: dict[str, object],
    ) -> None:
        assert color_id == self.color.id
        assert analysis_revision == "reference-v1"
        self.profiles.append(color_profile)

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

    async def rename(
        self,
        session: object,
        *,
        color_id: UUID,
        display_name: str,
        owner_id: int | None = None,
    ) -> Color:
        del session
        assert color_id == self.color.id
        assert owner_id == 42
        self.renamed.append(display_name)
        self.color.display_name = display_name
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
async def test_empty_bot_name_uses_detected_label_without_overriding_user_name() -> (
    None
):
    repository = Repository()

    async def moderate(data: bytes) -> ModerationResult:
        del data
        return ModerationResult(
            ModerationDisposition.APPROVED,
            "approved",
            98,
            97,
            suggested_display_name="TPU Dream Grey Charm Purple TPU-Z060",
        )

    service = CustomColorService(
        storage=Storage(),
        repository=repository,
        normalize=lambda data, mime: CanonicalImage(
            b"png", "image/png", 80, 60, "d" * 64
        ),
        moderate=moderate,
        moderation_model="vision-model",
    )

    detected = await service.create(
        Session(),
        owner_id=42,
        display_name="",
        upload=b"source",
        declared_mime="image/png",
        idempotency_key="detected-name",
    )
    assert detected.display_name == "TPU Dream Grey Charm Purple TPU-Z060"
    assert repository.renamed == ["TPU Dream Grey Charm Purple TPU-Z060"]

    repository.renamed.clear()
    await service.create(
        Session(),
        owner_id=42,
        display_name="Owner name",
        upload=b"source",
        declared_mime="image/png",
        idempotency_key="owner-name",
    )
    assert repository.renamed == []


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


@pytest.mark.asyncio
async def test_selected_structure_and_finish_persist_analyzed_profile() -> None:
    repository = Repository()

    async def moderate(data: bytes) -> ModerationResult:
        return ModerationResult(
            ModerationDisposition.APPROVED,
            "approved",
            98,
            97,
        )

    def analyze(
        data: bytes,
        structure: ColorStructure,
        finish: SurfaceFinish,
        result: ModerationResult,
    ) -> ReferenceProfile:
        assert structure is ColorStructure.SOLID
        assert finish is SurfaceFinish.MATTE
        assert result.disposition is ModerationDisposition.APPROVED
        return ReferenceProfile(
            structure,
            finish,
            92,
            (
                ColorCluster(
                    "#C83228",
                    (45.0, 58.0, 43.0),
                    1.0,
                    (100, 100, 200, 200),
                ),
            ),
        )

    service = CustomColorService(
        storage=Storage(),
        repository=repository,
        normalize=lambda data, mime: CanonicalImage(
            b"png", "image/png", 80, 60, "d" * 64
        ),
        moderate=moderate,
        analyze=analyze,
        moderation_model="vision-model",
    )
    color = await service.create(
        Session(),
        owner_id=42,
        display_name="Red",
        upload=b"source",
        declared_mime="image/png",
        idempotency_key="request-profile",
        color_structure="solid",
        finish="matte",
    )

    assert color.status == "approved"
    version = repository.created[0]
    assert version.color_structure == "solid"
    assert version.finish == "matte"
    assert repository.profiles[0]["base_rgb_hex"] == "#C83228"


@pytest.mark.asyncio
async def test_uncertain_analysis_prevents_automatic_approval() -> None:
    repository = Repository()

    async def moderate(data: bytes) -> ModerationResult:
        return ModerationResult(
            ModerationDisposition.APPROVED,
            "approved",
            98,
            97,
        )

    def uncertain(*args: object) -> ReferenceProfile:
        from car_wrap.custom_colors.analysis import ReferenceAnalysisError

        raise ReferenceAnalysisError("uncertain")

    service = CustomColorService(
        storage=Storage(),
        repository=repository,
        normalize=lambda data, mime: CanonicalImage(
            b"png", "image/png", 80, 60, "d" * 64
        ),
        moderate=moderate,
        analyze=uncertain,
        moderation_model="vision-model",
    )
    color = await service.create(
        Session(),
        owner_id=42,
        display_name="Unknown",
        upload=b"source",
        declared_mime="image/png",
        idempotency_key="request-uncertain",
        color_structure="multicolor",
        finish="satin",
    )

    assert color.status == "needs_review"
    assert repository.profiles == []
    assert repository.applied[0][2] == "needs_review"
