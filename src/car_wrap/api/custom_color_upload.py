"""Bounded in-memory multipart parsing for one custom-color upload."""

from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.parser import BytesParser

from fastapi import HTTPException, Request


@dataclass(frozen=True, slots=True)
class CustomColorUpload:
    name: str
    image: bytes
    mime_type: str
    color_structure: str
    finish: str


async def _bounded_body(request: Request, maximum: int) -> bytes:
    raw_length = request.headers.get("content-length")
    if raw_length is not None and (
        not raw_length.isascii()
        or not raw_length.isdecimal()
        or int(raw_length) > maximum
    ):
        raise HTTPException(status_code=413, detail="Upload is too large")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > maximum:
            raise HTTPException(status_code=413, detail="Upload is too large")
        body.extend(chunk)
    return bytes(body)


async def parse_custom_color_upload(request: Request) -> CustomColorUpload:
    """Accept exactly one UTF-8 name and one allowlisted image without disk spooling."""

    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data;"):
        raise HTTPException(status_code=415, detail="Multipart form is required")

    settings = request.app.state.settings
    body = await _bounded_body(
        request,
        settings.custom_color_max_bytes + 64 * 1024,
    )
    message = BytesParser(policy=policy.default).parsebytes(
        (f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode(
            "ascii",
            errors="strict",
        )
        + body
    )
    if not message.is_multipart():
        raise HTTPException(status_code=422, detail="Invalid upload")

    fields: dict[str, tuple[bytes, str | None, str | None]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        if (
            part.get_content_disposition() != "form-data"
            or not isinstance(name, str)
            or name in fields
            or not isinstance(payload, bytes)
        ):
            raise HTTPException(status_code=422, detail="Invalid upload")
        fields[name] = payload, part.get_content_type(), part.get_filename()

    field_names = set(fields)
    if field_names not in (
        {"name", "image"},
        {"name", "image", "color_structure", "finish"},
    ):
        raise HTTPException(status_code=422, detail="Invalid upload")
    name_bytes, _, name_filename = fields["name"]
    image, image_mime, image_filename = fields["image"]
    if (
        name_filename is not None
        or image_filename is None
        or not isinstance(image_mime, str)
        or image_mime not in settings.custom_color_mime_allowlist
        or len(image) > settings.custom_color_max_bytes
    ):
        raise HTTPException(status_code=422, detail="Invalid upload")
    try:
        name = name_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="Invalid upload") from None
    color_structure = "unspecified"
    finish = "unspecified"
    if "color_structure" in fields:
        structure_bytes, _, structure_filename = fields["color_structure"]
        finish_bytes, _, finish_filename = fields["finish"]
        if structure_filename is not None or finish_filename is not None:
            raise HTTPException(status_code=422, detail="Invalid upload")
        try:
            color_structure = structure_bytes.decode("ascii", errors="strict")
            finish = finish_bytes.decode("ascii", errors="strict")
        except UnicodeDecodeError:
            raise HTTPException(status_code=422, detail="Invalid upload") from None
        if color_structure not in {"solid", "multicolor"} or finish not in {
            "matte",
            "satin",
            "gloss",
        }:
            raise HTTPException(status_code=422, detail="Invalid upload")
    return CustomColorUpload(
        name=name.strip(),
        image=image,
        mime_type=image_mime,
        color_structure=color_structure,
        finish=finish,
    )
