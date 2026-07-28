"""Private custom color object storage contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from car_wrap.custom_colors.storage import FilesystemPrivateStorage


def test_put_read_delete_round_trip(tmp_path: Path) -> None:
    storage = FilesystemPrivateStorage(tmp_path / "private", max_object_bytes=64)
    stored = storage.put(b"canonical-png")

    assert stored.key.endswith(".png")
    assert len(stored.sha256) == 64
    assert storage.read(stored.key, stored.sha256) == b"canonical-png"

    storage.delete(stored.key)
    storage.delete(stored.key)
    with pytest.raises(FileNotFoundError):
        storage.read(stored.key, stored.sha256)


@pytest.mark.parametrize(
    "key",
    (
        "../secret.png",
        "/var/private/secret.png",
        "aa/bb/not-a-uuid.png",
        "AA/bb/0123456789abcdef0123456789abcdef.png",
    ),
)
def test_rejects_client_controlled_paths(tmp_path: Path, key: str) -> None:
    storage = FilesystemPrivateStorage(tmp_path / "private", max_object_bytes=64)

    with pytest.raises(ValueError):
        storage.read(key, "a" * 64)


def test_detects_tampering_and_size_limits(tmp_path: Path) -> None:
    root = tmp_path / "private"
    storage = FilesystemPrivateStorage(root, max_object_bytes=16)
    stored = storage.put(b"safe")
    object_path = root / stored.key
    object_path.write_bytes(b"changed")

    with pytest.raises(ValueError, match="integrity"):
        storage.read(stored.key, stored.sha256)
    with pytest.raises(ValueError, match="size"):
        storage.put(b"x" * 17)


def test_rejects_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "private"
    storage = FilesystemPrivateStorage(root, max_object_bytes=64)
    stored = storage.put(b"safe")
    object_path = root / stored.key
    object_path.unlink()
    object_path.symlink_to(tmp_path / "outside")

    with pytest.raises(ValueError):
        storage.read(stored.key, stored.sha256)
