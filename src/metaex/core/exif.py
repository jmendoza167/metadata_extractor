"""EXIF / TIFF metadata structures, shared by JPEG, PNG, WebP and TIFF.

EXIF data is a little TIFF file: an 8-byte header (byte order, the magic
number 42, the offset of the first IFD) followed by Image File Directories
(IFDs) of tagged values. All offsets are relative to the start of the TIFF
header, never to the start of the containing file.
"""

from __future__ import annotations

from dataclasses import dataclass

from metaex.core.errors import MalformedFileError
from metaex.core.reader import ByteReader

EXIF_APP1_PREFIX = b"Exif\x00"
TIFF_MAGIC = 42


@dataclass(frozen=True)
class TiffHeader:
    byte_order: str
    """"little" (II, Intel) or "big" (MM, Motorola)."""
    ifd0_offset: int


def tiff_from_exif_payload(payload: bytes) -> bytes:
    """Return the TIFF data inside an APP1 "Exif\\0\\0" payload.

    The identifier is "Exif" plus a NUL and one padding byte (normally
    another NUL, though some writers use 0xFF).
    """
    if not payload.startswith(EXIF_APP1_PREFIX) or len(payload) < 6:
        raise MalformedFileError('not an EXIF payload (expected "Exif\\0\\0")')
    return payload[6:]


def parse_tiff_header(tiff: bytes) -> TiffHeader:
    """Parse and validate the 8-byte TIFF header."""
    r = ByteReader(tiff)
    order = r.read(2, "TIFF byte order")
    if order == b"II":
        r.endian = "little"
    elif order == b"MM":
        r.endian = "big"
    else:
        raise MalformedFileError(f"invalid TIFF byte order {order!r} (expected II or MM)")
    magic = r.u16("TIFF magic number")
    if magic != TIFF_MAGIC:
        raise MalformedFileError(f"invalid TIFF magic number {magic} (expected 42)")
    ifd0_offset = r.u32("IFD0 offset")
    if ifd0_offset < 8 or ifd0_offset >= len(tiff):
        raise MalformedFileError(
            f"IFD0 offset {ifd0_offset} is outside the EXIF data ({len(tiff)} bytes)")
    return TiffHeader(r.endian, ifd0_offset)
