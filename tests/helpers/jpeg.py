"""Build small, structurally valid JPEG files for tests.

The images are not meant to decode to real pictures; they only need the
marker structure metaex reads.
"""

from __future__ import annotations

import struct

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"

# Entropy-coded data containing a stuffed 0xFF byte (FF00).
SCAN_DATA = b"\x12\x34\xff\x00\x56\x78"


def segment(marker: int, payload: bytes) -> bytes:
    """A marker segment with a correct length field."""
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def app(n: int, payload: bytes) -> bytes:
    return segment(0xE0 + n, payload)


def sof0(width: int = 8, height: int = 8) -> bytes:
    return segment(0xC0, struct.pack(">BHHB", 8, height, width, 1) + b"\x01\x11\x00")


def dqt() -> bytes:
    return segment(0xDB, b"\x00" + bytes(64))


def dht() -> bytes:
    return segment(0xC4, b"\x00" + bytes(16))


def sos() -> bytes:
    return segment(0xDA, b"\x01\x01\x00\x00\x3f\x00")


def make_jpeg(*segments: bytes, width: int = 8, height: int = 8,
              scan: bytes = SCAN_DATA, eoi: bool = True, trailing: bytes = b"") -> bytes:
    """SOI, the given segments, a minimal frame and scan, EOI, trailing bytes."""
    return (SOI + b"".join(segments) + dqt() + sof0(width, height) + dht() + sos()
            + scan + (EOI if eoi else b"") + trailing)
