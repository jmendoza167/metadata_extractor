"""Build small WebP files for tests.

Lossless images are encoded here (VP8L with "simple" prefix codes: one
fixed color, or two colors that differ in green, one bit per pixel). Lossy
and lossy+alpha images use the 1x1 sample bitstreams from the WebP
feature-detection images Google publishes; they are generated test data,
not photos.
"""

from __future__ import annotations

import base64
import struct

ICC_FLAG, ALPHA_FLAG, EXIF_FLAG, XMP_FLAG, ANIMATION_FLAG = 0x20, 0x10, 0x08, 0x04, 0x02


def chunk(fourcc: bytes | str, payload: bytes = b"", pad: bytes = b"\x00") -> bytes:
    """One chunk; ``pad`` is the padding byte written after an odd payload."""
    if isinstance(fourcc, str):
        fourcc = fourcc.encode("latin-1")
    assert len(fourcc) == 4
    return fourcc + struct.pack("<I", len(payload)) + payload + (pad if len(payload) % 2 else b"")


def riff(body: bytes, trailing: bytes = b"", size: int | None = None) -> bytes:
    """"RIFF" + size + "WEBP" + body (+ bytes after the RIFF data)."""
    if size is None:
        size = 4 + len(body)
    return b"RIFF" + struct.pack("<I", size) + b"WEBP" + body + trailing


# -- bitstreams --------------------------------------------------------------------------

_SAMPLE_LOSSY = "UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA"
_SAMPLE_ALPHA = ("UklGRkoAAABXRUJQVlA4WAoAAAAQAAAAAAAAAAAAQUxQSAwAAAARBxAR/Q9ERP8DAABWUDgg"
                 "GAAAABQBAJ0BKgEAAQAAAP4AAA3AAP7mtQAAAA==")


def _sample_chunks(b64: str) -> dict[bytes, bytes]:
    data = base64.b64decode(b64)
    out, pos = {}, 12
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        out[data[pos : pos + 4]] = data[pos + 8 : pos + 8 + size]
        pos += 8 + size + (size & 1)
    return out


VP8_1X1 = _sample_chunks(_SAMPLE_LOSSY)[b"VP8 "]
"""A real 1x1 lossy key frame."""
ALPHA_1X1 = _sample_chunks(_SAMPLE_ALPHA)
"""The chunks of a real 1x1 lossy image with alpha (VP8X, ALPH, "VP8 ")."""


class _Bits:
    def __init__(self) -> None:
        self.value = 0
        self.n = 0

    def put(self, value: int, bits: int) -> None:
        assert 0 <= value < (1 << bits) or bits == 0
        self.value |= value << self.n
        self.n += bits

    def bytes(self) -> bytes:
        return self.value.to_bytes((self.n + 7) // 8, "little")


def _simple_code(bits: _Bits, symbols: tuple[int, ...]) -> None:
    """A "simple" prefix code with one or two 8-bit symbols."""
    bits.put(1, 1)  # simple code
    bits.put(len(symbols) - 1, 1)
    bits.put(1, 1)  # first symbol uses 8 bits
    for s in symbols:
        bits.put(s, 8)


def vp8l(width: int, height: int, argb: tuple[int, int, int, int] = (255, 200, 100, 50),
         green2: int | None = None, alpha_used: bool = False) -> bytes:
    """A VP8L bitstream: every pixel ``argb``; with ``green2``, a checkerboard of
    two greens (one bit per pixel)."""
    assert 1 <= width <= 16384 and 1 <= height <= 16384
    a, r, g, b = argb
    bits = _Bits()
    bits.put(0x2F, 8)
    bits.put(width - 1, 14)
    bits.put(height - 1, 14)
    bits.put(int(alpha_used), 1)
    bits.put(0, 3)  # version
    bits.put(0, 1)  # no transform
    bits.put(0, 1)  # no color cache
    bits.put(0, 1)  # no meta prefix codes
    greens = (g,) if green2 is None else tuple(sorted((g, green2)))
    _simple_code(bits, greens)  # green
    _simple_code(bits, (r,))
    _simple_code(bits, (b,))
    _simple_code(bits, (a,))
    _simple_code(bits, (0,))  # distance (unused)
    if green2 is not None:
        for y in range(height):
            for x in range(width):
                bits.put((x + y) % 2, 1)  # code 0 = the smaller green
    return bits.bytes()


def vp8l_size(bitstream: bytes) -> tuple[int, int]:
    v = int.from_bytes(bitstream[1:5], "little")
    return (v & 0x3FFF) + 1, ((v >> 14) & 0x3FFF) + 1


# -- chunks ------------------------------------------------------------------------------


def vp8x(flags: int, width: int, height: int) -> bytes:
    return chunk("VP8X", bytes([flags, 0, 0, 0]) + (width - 1).to_bytes(3, "little")
                 + (height - 1).to_bytes(3, "little"))


def anim(loops: int = 0, background: bytes = b"\xff\xff\xff\xff") -> bytes:
    return chunk("ANIM", background + struct.pack("<H", loops))


def anmf(*frame_chunks: bytes, x: int = 0, y: int = 0, width: int = 4, height: int = 4,
         duration: int = 100, flags: int = 0) -> bytes:
    header = ((x // 2).to_bytes(3, "little") + (y // 2).to_bytes(3, "little")
              + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
              + duration.to_bytes(3, "little") + bytes([flags]))
    return chunk("ANMF", header + b"".join(frame_chunks))


def exif_chunk(tiff: bytes, prefix: bool = False) -> bytes:
    return chunk("EXIF", (b"Exif\x00\x00" if prefix else b"") + tiff)


def xmp_chunk(packet: bytes | str) -> bytes:
    return chunk("XMP ", packet.encode("utf-8") if isinstance(packet, str) else packet)


def iccp(profile: bytes) -> bytes:
    return chunk("ICCP", profile)


# -- files -------------------------------------------------------------------------------


def _fourccs(blob: bytes) -> list[bytes]:
    out, pos = [], 0
    while pos + 8 <= len(blob):
        size = int.from_bytes(blob[pos + 4 : pos + 8], "little")
        out.append(blob[pos : pos + 4])
        if blob[pos : pos + 4] == b"ANMF":
            out += _fourccs(blob[pos + 24 : pos + 8 + size])
        pos += 8 + size + (size & 1)
    return out


def lossless(width: int = 4, height: int = 4, **kwargs) -> bytes:
    """A simple (VP8L only) WebP."""
    return riff(chunk("VP8L", vp8l(width, height, **kwargs)))


def lossy() -> bytes:
    """A simple (VP8 only) 1x1 WebP."""
    return riff(chunk("VP8 ", VP8_1X1))


def extended(*chunks: bytes, width: int | None = None, height: int | None = None,
             flags: int | None = None, trailing: bytes = b"", size: int | None = None) -> bytes:
    """An extended WebP: VP8X (flags computed from the chunks unless given;
    the canvas is the first image's size unless given) followed by ``chunks``
    in the order given."""
    body = b"".join(chunks)
    names = _fourccs(body)
    if flags is None:
        flags = 0
        for fourcc, bit in ((b"ICCP", ICC_FLAG), (b"EXIF", EXIF_FLAG), (b"XMP ", XMP_FLAG),
                            (b"ANIM", ANIMATION_FLAG), (b"ALPH", ALPHA_FLAG)):
            if fourcc in names:
                flags |= bit
    if width is None or height is None:
        width, height = 4, 4
        pos = body.find(b"VP8L")
        if pos >= 0:
            width, height = vp8l_size(body[pos + 8 : pos + 13])
        elif b"VP8 " in names:
            width = height = 1
    return riff(vp8x(flags, width, height) + body, trailing, size)
