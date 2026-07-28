"""Private, integrity-checked storage for canonical color references."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

_KEY_PATTERN = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{32}\.png$")


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    sha256: str
    byte_size: int


class PrivateStorage(Protocol):
    def put(self, data: bytes) -> StoredObject: ...

    def read(self, key: str, expected_sha256: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


class FilesystemPrivateStorage:
    """Filesystem adapter whose keys never contain client-controlled data."""

    def __init__(self, root: Path, *, max_object_bytes: int) -> None:
        resolved = root.resolve()
        if not root.is_absolute() or resolved == Path("/"):
            raise ValueError("storage root must be an absolute narrow directory")
        if max_object_bytes <= 0:
            raise ValueError("maximum object size must be positive")
        self._root = resolved
        self._max_object_bytes = max_object_bytes

    def put(self, data: bytes) -> StoredObject:
        if not data or len(data) > self._max_object_bytes:
            raise ValueError("canonical object size is outside configured limits")
        digest = hashlib.sha256(data).hexdigest()
        token = uuid4().hex
        key = f"{token[:2]}/{token[2:4]}/{token}.png"
        destination = self._path_for(key)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".pending-",
            dir=destination.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return StoredObject(key=key, sha256=digest, byte_size=len(data))

    def read(self, key: str, expected_sha256: str) -> bytes:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError("invalid expected digest")
        path = self._path_for(key)
        if path.is_symlink():
            raise ValueError("symbolic links are not accepted")
        data = path.read_bytes()
        if len(data) > self._max_object_bytes:
            raise ValueError("stored object exceeds configured limit")
        observed = hashlib.sha256(data).hexdigest()
        if not hmac.compare_digest(observed, expected_sha256):
            raise ValueError("stored object integrity check failed")
        return data

    def delete(self, key: str) -> None:
        path = self._path_for(key)
        if path.is_symlink():
            raise ValueError("symbolic links are not accepted")
        path.unlink(missing_ok=True)

    def _path_for(self, key: str) -> Path:
        if not _KEY_PATTERN.fullmatch(key):
            raise ValueError("invalid private object key")
        candidate = (self._root / key).resolve()
        if not candidate.is_relative_to(self._root):
            raise ValueError("private object key escapes storage root")
        return candidate
