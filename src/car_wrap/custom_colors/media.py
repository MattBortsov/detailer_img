"""Bounded malware scanning and canonical image normalization."""

from __future__ import annotations

import hashlib
import multiprocessing
import socket
import struct
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError


class MediaValidationError(ValueError):
    """The uploaded bytes are not an acceptable single image."""


class MalwareDetectedError(MediaValidationError):
    """ClamAV identified a threat."""


class ScanUnavailableError(MediaValidationError):
    """The malware scanner could not produce a clean verdict."""


class MalwareScanner(Protocol):
    def scan(self, data: bytes) -> None: ...


@dataclass(frozen=True, slots=True)
class MediaPolicy:
    max_bytes: int = 8 * 1024 * 1024
    max_side_px: int = 8192
    max_pixels: int = 20_000_000
    max_frames: int = 1
    output_long_edge_px: int = 2048
    decode_timeout_seconds: float = 15.0
    worker_memory_bytes: int = 512 * 1024 * 1024

    def __post_init__(self) -> None:
        values = (
            self.max_bytes,
            self.max_side_px,
            self.max_pixels,
            self.max_frames,
            self.output_long_edge_px,
            self.decode_timeout_seconds,
            self.worker_memory_bytes,
        )
        if any(value <= 0 for value in values):
            raise ValueError("media policy limits must be positive")
        if self.max_frames != 1:
            raise ValueError("custom references must contain one frame")
        if self.output_long_edge_px > self.max_side_px:
            raise ValueError("output edge exceeds decode side limit")


@dataclass(frozen=True, slots=True)
class CanonicalImage:
    data: bytes
    media_type: str
    width: int
    height: int
    sha256: str


class ClamdInstreamScanner:
    """Private Unix-socket ClamAV INSTREAM client."""

    def __init__(
        self,
        socket_path: Path,
        *,
        max_bytes: int,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not socket_path.is_absolute():
            raise ValueError("ClamAV socket path must be absolute")
        if max_bytes <= 0 or timeout_seconds <= 0:
            raise ValueError("ClamAV limits must be positive")
        self._socket_path = socket_path
        self._max_bytes = max_bytes
        self._timeout_seconds = timeout_seconds

    def scan(self, data: bytes) -> None:
        if not data or len(data) > self._max_bytes:
            raise MediaValidationError("image byte limit exceeded")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(self._timeout_seconds)
                client.connect(str(self._socket_path))
                client.sendall(b"zINSTREAM\0")
                view = memoryview(data)
                for offset in range(0, len(view), 64 * 1024):
                    chunk = view[offset : offset + 64 * 1024]
                    client.sendall(struct.pack(">I", len(chunk)))
                    client.sendall(chunk)
                client.sendall(struct.pack(">I", 0))
                response = client.recv(4096)
        except (OSError, TimeoutError):
            raise ScanUnavailableError("malware scan unavailable") from None
        verdict = response.rstrip(b"\0\r\n")
        if verdict.endswith(b": OK"):
            return
        if verdict.endswith(b" FOUND"):
            raise MalwareDetectedError("uploaded image failed malware scan")
        raise ScanUnavailableError("malware scanner returned no clean verdict")


def _detected_mimes(data: bytes) -> frozenset[str]:
    if data.startswith(b"\xff\xd8\xff"):
        return frozenset({"image/jpeg"})
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return frozenset({"image/png"})
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return frozenset({"image/webp"})
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in {
            b"heic",
            b"heix",
            b"hevc",
            b"hevx",
            b"heim",
            b"heis",
            b"mif1",
            b"msf1",
        }:
            return frozenset({"image/heic", "image/heif"})
    raise MediaValidationError("unsupported image signature")


def _register_heif() -> None:
    try:
        from pillow_heif import register_heif_opener
    except ImportError:
        raise MediaValidationError("HEIC/HEIF decoder is unavailable") from None
    register_heif_opener(thumbnails=False)


def _fresh_rgb(image: Image.Image) -> Image.Image:
    profile = image.info.get("icc_profile")
    oriented = ImageOps.exif_transpose(image)
    if profile:
        try:
            source_profile = ImageCms.ImageCmsProfile(BytesIO(profile))
            target_profile = ImageCms.createProfile("sRGB")
            converted_result = ImageCms.profileToProfile(
                oriented,
                source_profile,
                target_profile,
                outputMode="RGBA" if "A" in oriented.getbands() else "RGB",
            )
            if converted_result is None:
                raise MediaValidationError("color profile conversion failed")
            converted = converted_result
        except (OSError, ValueError):
            raise MediaValidationError("invalid embedded color profile") from None
    else:
        converted = oriented.convert("RGBA" if "A" in oriented.getbands() else "RGB")
    target = Image.new("RGB", converted.size, "white")
    if converted.mode == "RGBA":
        target.paste(converted, mask=converted.getchannel("A"))
    else:
        target.paste(converted)
    return target


def _decode_canonical(data: bytes, policy: MediaPolicy) -> CanonicalImage:
    if data[4:8] == b"ftyp":
        _register_heif()
    original_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = policy.max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
                if (
                    width <= 0
                    or height <= 0
                    or width > policy.max_side_px
                    or height > policy.max_side_px
                ):
                    raise MediaValidationError("image side limit exceeded")
                if width * height > policy.max_pixels:
                    raise MediaValidationError("image pixel limit exceeded")
                if getattr(image, "n_frames", 1) != policy.max_frames:
                    raise MediaValidationError("image frame count is not supported")
                image.seek(0)
                image.load()
                canonical = _fresh_rgb(image)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise MediaValidationError("image pixel limit exceeded") from None
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise MediaValidationError("image cannot be safely decoded") from None
    finally:
        Image.MAX_IMAGE_PIXELS = original_limit
    longest = max(canonical.size)
    if longest > policy.output_long_edge_px:
        scale = policy.output_long_edge_px / longest
        canonical = canonical.resize(
            (
                max(1, round(canonical.width * scale)),
                max(1, round(canonical.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
    output = BytesIO()
    canonical.save(output, format="PNG", optimize=True)
    encoded = output.getvalue()
    return CanonicalImage(
        data=encoded,
        media_type="image/png",
        width=canonical.width,
        height=canonical.height,
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


def _set_resource_limits(policy: MediaPolicy) -> None:
    try:
        import resource

        resource.setrlimit(
            resource.RLIMIT_AS,
            (policy.worker_memory_bytes, policy.worker_memory_bytes),
        )
        cpu_seconds = max(1, round(policy.decode_timeout_seconds) + 1)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    except (ImportError, OSError, ValueError):
        # Non-POSIX tests still retain byte/pixel/time limits. Production is Linux.
        return


def _worker(
    send: Connection,
    data: bytes,
    policy: MediaPolicy,
) -> None:
    try:
        _set_resource_limits(policy)
        send.send((True, _decode_canonical(data, policy)))
    except BaseException as error:
        send.send((False, type(error).__name__))
    finally:
        send.close()


def _isolated_decode(data: bytes, policy: MediaPolicy) -> CanonicalImage:
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(send, data, policy), daemon=True)
    process.start()
    send.close()
    try:
        if not receive.poll(policy.decode_timeout_seconds):
            process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
            raise MediaValidationError("image decode timed out")
        ok, value = receive.recv()
    except EOFError:
        raise MediaValidationError("image decoder stopped unexpectedly") from None
    finally:
        receive.close()
        process.join(timeout=1)
    if not ok or not isinstance(value, CanonicalImage):
        raise MediaValidationError("image cannot be safely decoded")
    return value


def normalize_reference(
    data: bytes,
    *,
    declared_mime: str,
    scanner: MalwareScanner,
    policy: MediaPolicy,
    isolated: bool = True,
) -> CanonicalImage:
    """Validate, scan and freshly render exactly one color reference."""

    if not data or len(data) > policy.max_bytes:
        raise MediaValidationError("image byte limit exceeded")
    detected = _detected_mimes(data)
    if declared_mime not in detected:
        raise MediaValidationError("declared MIME does not match image bytes")
    scanner.scan(data)
    decode: Callable[[bytes, MediaPolicy], CanonicalImage] = (
        _isolated_decode if isolated else _decode_canonical
    )
    return decode(data, policy)
