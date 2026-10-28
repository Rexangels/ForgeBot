"""Local-only reference-image probing (no pixels leave the machine).

Reads container dimensions with the standard library only, so the design
pipeline can describe reference views to the model as text (view label,
format, pixel size, byte size) without uploading image bytes. Pillow stays
an optional future enhancement for thumbnails; nothing here requires it.
"""

from __future__ import annotations

import struct


class LocalVisionError(ValueError):
    """The reference bytes are not a readable image container."""


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise LocalVisionError("Unreadable PNG header.")
    width, height = struct.unpack(">II", data[16:24])
    if width < 1 or height < 1:
        raise LocalVisionError("Unusable PNG dimensions.")
    return width, height


def _gif_size(data: bytes) -> tuple[int, int]:
    if len(data) < 10:
        raise LocalVisionError("Unreadable GIF header.")
    width, height = struct.unpack("<HH", data[6:10])
    if width < 1 or height < 1:
        raise LocalVisionError("Unusable GIF dimensions.")
    return width, height


def _bmp_size(data: bytes) -> tuple[int, int]:
    if len(data) < 26:
        raise LocalVisionError("Unreadable BMP header.")
    width, height = struct.unpack("<ii", data[18:26])
    if width < 1 or height < 1:
        raise LocalVisionError("Unusable BMP dimensions.")
    return width, abs(height)


def _jpeg_size(data: bytes) -> tuple[int, int]:
    # Walk markers to the first start-of-frame carrying dimensions.
    pos = 2
    end = len(data)
    while pos + 9 <= end:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            pos += 2
            continue
        if pos + 4 > end:
            break
        (length,) = struct.unpack(">H", data[pos + 2:pos + 4])
        if length < 2 or pos + length > end:
            break
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[pos + 5:pos + 9])
            if width < 1 or height < 1:
                raise LocalVisionError("Unusable JPEG dimensions.")
            return width, height
        pos += 2 + length
    raise LocalVisionError("No JPEG frame header found.")


def _webp_size(data: bytes) -> tuple[int, int]:
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise LocalVisionError("Unreadable WebP header.")
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30:
        width, height = struct.unpack("<HH", data[26:30])
        width &= 0x3FFF
        height &= 0x3FFF
        if width < 1 or height < 1:
            raise LocalVisionError("Unusable WebP dimensions.")
        return width, height
    if chunk == b"VP8L" and len(data) >= 25:
        bits = struct.unpack("<I", data[21:25])[0]
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return width, height
    raise LocalVisionError("Unsupported WebP chunk for local probing.")


_PROBERS = (
    ("png", b"\x89PNG\r\n\x1a\n", _png_size),
    ("jpeg", b"\xff\xd8\xff", _jpeg_size),
    ("gif", b"GIF87a", _gif_size),
    ("gif", b"GIF89a", _gif_size),
    ("bmp", b"BM", _bmp_size),
    ("webp", b"RIFF", _webp_size),
)


def probe_image(data: bytes) -> dict:
    """Return local facts about reference bytes without decoding pixels.

    Result: {"format", "width_px", "height_px", "bytes"}. Raises
    LocalVisionError when the container is unknown or truncated.
    """
    blob = bytes(data or b"")
    if not blob:
        raise LocalVisionError("Empty reference image.")
    detected = [name for name, magic, _ in _PROBERS if blob.startswith(magic)]
    if "webp" in detected and not (blob[8:12] == b"WEBP"):
        detected.remove("webp")
    if not detected:
        raise LocalVisionError("Unsupported image container for local probing.")
    kind = detected[0]
    prober = next(func for name, _, func in _PROBERS if name == kind)
    width, height = prober(blob)
    return {"format": kind, "width_px": width, "height_px": height, "bytes": len(blob)}


def describe_view(label: str, probe: dict, index: int) -> str:
    """One text line per reference view for the text-only design brief."""
    return (
        f"View {index + 1} ({label}): {probe['format']} "
        f"{probe['width_px']}x{probe['height_px']} px, {probe['bytes']} bytes, "
        "kept on this machine; no pixels from this view were uploaded."
    )
