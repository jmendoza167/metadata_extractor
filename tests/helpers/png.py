"""Build small, real PNG files for tests (pixel data compressed with zlib)."""

from __future__ import annotations

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"

GRAY, RGB, PALETTE, GRAY_ALPHA, RGBA = 0, 2, 3, 4, 6
_CHANNELS = {GRAY: 1, RGB: 3, PALETTE: 1, GRAY_ALPHA: 2, RGBA: 4}


def chunk(chunk_type: bytes | str, data: bytes = b"", crc: int | None = None) -> bytes:
    """One chunk with a correct CRC (unless ``crc`` is given)."""
    if isinstance(chunk_type, str):
        chunk_type = chunk_type.encode("ascii")
    if crc is None:
        crc = zlib.crc32(chunk_type + data)
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def ihdr(width: int = 4, height: int = 4, depth: int = 8, color: int = RGB,
         interlace: int = 0) -> bytes:
    return chunk("IHDR", struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace))


def _row_bytes(width: int, depth: int, color: int) -> int:
    return (width * _CHANNELS[color] * depth + 7) // 8


def _pixels(width: int, height: int, depth: int, color: int, seed: int) -> bytes:
    """Unfiltered scanlines (filter byte 0 + a deterministic pattern)."""
    rows = []
    n = _row_bytes(width, depth, color)
    for y in range(height):
        rows.append(b"\x00" + bytes((seed + 7 * x + 13 * y) % 256 for x in range(n)))
    return b"".join(rows)


def raw_image(width: int = 4, height: int = 4, depth: int = 8, color: int = RGB,
              seed: int = 1) -> bytes:
    """Uncompressed image data for a non-interlaced image."""
    return _pixels(width, height, depth, color, seed)


def idat_chunks(raw: bytes, parts: int = 1) -> list[bytes]:
    """The zlib-compressed image data split over ``parts`` IDAT chunks."""
    compressed = zlib.compress(raw, 9)
    size = -(-len(compressed) // parts)
    return [chunk("IDAT", compressed[i : i + size]) for i in range(0, len(compressed), size)]


def make_png(*chunks: bytes, width: int = 4, height: int = 4, depth: int = 8,
             color: int = RGB, before_idat: tuple[bytes, ...] = (), idat_parts: int = 1,
             iend: bool = True, trailing: bytes = b"", seed: int = 1) -> bytes:
    """Signature, IHDR, ``before_idat`` chunks, IDAT(s), ``chunks``, IEND, trailing bytes.

    For palette images, pass PLTE (and tRNS) in ``before_idat``.
    """
    raw = raw_image(width, height, depth, color, seed)
    return (SIGNATURE + ihdr(width, height, depth, color)
            + b"".join(before_idat)
            + b"".join(idat_chunks(raw, idat_parts))
            + b"".join(chunks)
            + (chunk("IEND") if iend else b"")
            + trailing)


def text(keyword: str, value: str) -> bytes:
    return chunk("tEXt", keyword.encode("latin-1") + b"\x00" + value.encode("latin-1"))


def ztxt(keyword: str, value: str, method: int = 0, compressed: bytes | None = None) -> bytes:
    body = compressed if compressed is not None else zlib.compress(value.encode("latin-1"))
    return chunk("zTXt", keyword.encode("latin-1") + b"\x00" + bytes([method]) + body)


def itxt(keyword: str, value: str, language: str = "", translated: str = "",
         compress: bool = False, compressed: bytes | None = None) -> bytes:
    body = value.encode("utf-8")
    if compressed is not None:
        body, compress = compressed, True
    elif compress:
        body = zlib.compress(body)
    return chunk("iTXt", keyword.encode("latin-1") + b"\x00" + bytes([int(compress), 0])
                 + language.encode("ascii") + b"\x00" + translated.encode("utf-8") + b"\x00"
                 + body)


def zlib_bomb(size: int) -> bytes:
    """zlib data that expands to ``size`` zero bytes."""
    c = zlib.compressobj(9)
    out = bytearray()
    block = bytes(1024 * 1024)
    left = size
    while left > 0:
        out += c.compress(block[: min(left, len(block))])
        left -= len(block)
    return bytes(out + c.flush())


def iccp(profile: bytes, name: str = "ICC profile", compressed: bytes | None = None) -> bytes:
    body = compressed if compressed is not None else zlib.compress(profile)
    return chunk("iCCP", name.encode("latin-1") + b"\x00\x00" + body)


def exif_chunk(tiff: bytes, prefix: bool = False) -> bytes:
    return chunk("eXIf", (b"Exif\x00\x00" if prefix else b"") + tiff)


def time_chunk(year=2024, month=5, day=1, hour=12, minute=30, second=0) -> bytes:
    return chunk("tIME", struct.pack(">HBBBBB", year, month, day, hour, minute, second))


def phys(x: int = 2835, y: int = 2835, unit: int = 1) -> bytes:
    return chunk("pHYs", struct.pack(">IIB", x, y, unit))


def actl(frames: int, plays: int = 0) -> bytes:
    return chunk("acTL", struct.pack(">II", frames, plays))


def fctl(sequence: int, width: int = 4, height: int = 4) -> bytes:
    return chunk("fcTL", struct.pack(">IIIIIHHBB", sequence, width, height, 0, 0, 1, 10, 0, 0))


def fdat(sequence: int, raw: bytes) -> bytes:
    return chunk("fdAT", struct.pack(">I", sequence) + zlib.compress(raw))
