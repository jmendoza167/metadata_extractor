"""Build TIFF / EXIF structures for tests, in either byte order.

Entries are (tag, type, value) tuples. Values are encoded according to the
TIFF type: str for ASCII, bytes for BYTE/UNDEFINED, ints or floats (or a
list of them) for numeric types, (numerator, denominator) pairs (or a list
of pairs) for RATIONAL/SRATIONAL. A (tag, type, count, raw_bytes) 4-tuple
writes the value bytes exactly as given, for malformed cases.
"""

from __future__ import annotations

import struct

BYTE, ASCII, SHORT, LONG, RATIONAL, SBYTE, UNDEFINED, SSHORT, SLONG, SRATIONAL, FLOAT, DOUBLE = range(1, 13)

_FORMATS = {BYTE: "B", SHORT: "H", LONG: "I", SBYTE: "b", SSHORT: "h", SLONG: "i",
            FLOAT: "f", DOUBLE: "d", RATIONAL: "I", SRATIONAL: "i"}

EXIF_POINTER = 0x8769
GPS_POINTER = 0x8825
INTEROP_POINTER = 0xA005
THUMBNAIL_OFFSET = 0x0201
THUMBNAIL_LENGTH = 0x0202


def _bo(order: str) -> str:
    return "<" if order in ("<", "II", "little") else ">"


def encode_value(typ: int, value, order: str = "<") -> tuple[int, bytes]:
    """Return (count, bytes) for a value of the given TIFF type."""
    bo = _bo(order)
    if typ == ASCII:
        data = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        if not data.endswith(b"\x00"):
            data += b"\x00"
        return len(data), data
    if isinstance(value, (bytes, bytearray)):
        return len(value), bytes(value)
    values = list(value) if isinstance(value, (list, tuple)) and not _is_pair(typ, value) else [value]
    if typ in (RATIONAL, SRATIONAL):
        flat = [x for pair in values for x in pair]
        return len(values), struct.pack(bo + _FORMATS[typ] * len(flat), *flat)
    return len(values), struct.pack(bo + _FORMATS[typ] * len(values), *values)


def _is_pair(typ: int, value) -> bool:
    return typ in (RATIONAL, SRATIONAL) and len(value) == 2 and all(isinstance(v, int) for v in value)


def ifd_bytes(entries, start: int, order: str = "<", next_offset: int = 0) -> bytes:
    """One IFD placed at offset ``start``, followed by its out-of-line values."""
    bo = _bo(order)
    data_start = start + 2 + 12 * len(entries) + 4
    out = bytearray(struct.pack(bo + "H", len(entries)))
    data_area = bytearray()
    for e in entries:
        if len(e) == 4:
            tag, typ, count, data = e
        else:
            tag, typ, value = e
            count, data = encode_value(typ, value, order)
        if len(data) <= 4:
            field = data.ljust(4, b"\x00")
        else:
            field = struct.pack(bo + "I", data_start + len(data_area))
            data_area += data
            if len(data_area) % 2:
                data_area += b"\x00"
        out += struct.pack(bo + "HHI", tag, typ, count) + field
    out += struct.pack(bo + "I", next_offset)
    return bytes(out + data_area)


def tiff_header(order: str = "<", ifd0_offset: int = 8) -> bytes:
    bo = _bo(order)
    return (b"II" if bo == "<" else b"MM") + struct.pack(bo + "HI", 42, ifd0_offset)


def build_tiff(ifd0=(), *, order: str = "<", exif=None, gps=None, interop=None,
               ifd1=None, thumbnail: bytes | None = None) -> bytes:
    """A complete TIFF structure with optional Exif, GPS, Interop IFDs and IFD1.

    Pointer tags (0x8769, 0x8825, 0xA005, 0x0201/0x0202) are added
    automatically with the correct offsets.
    """
    def layout(ptr: dict) -> tuple[bytes, dict]:
        ifd0_e = list(ifd0)
        if exif is not None:
            ifd0_e.append((EXIF_POINTER, LONG, ptr.get("exif", 0)))
        if gps is not None:
            ifd0_e.append((GPS_POINTER, LONG, ptr.get("gps", 0)))
        exif_e = None
        if exif is not None:
            exif_e = list(exif)
            if interop is not None:
                exif_e.append((INTEROP_POINTER, LONG, ptr.get("interop", 0)))
        ifd1_e = None
        if ifd1 is not None or thumbnail is not None:
            ifd1_e = list(ifd1 or [])
            if thumbnail is not None:
                ifd1_e += [(THUMBNAIL_OFFSET, LONG, ptr.get("thumb", 0)),
                           (THUMBNAIL_LENGTH, LONG, len(thumbnail))]
        parts = [("ifd0", ifd0_e), ("exif", exif_e), ("gps", gps and list(gps)),
                 ("interop", interop and list(interop)), ("ifd1", ifd1_e)]
        out = bytearray(tiff_header(order))
        offsets = {}
        for name, entries in parts:
            if entries is None:
                continue
            offsets[name] = len(out)
            nxt = ptr.get("ifd1", 0) if name == "ifd0" else 0
            out += ifd_bytes(entries, len(out), order, nxt)
            if len(out) % 2:
                out += b"\x00"
        if thumbnail is not None:
            offsets["thumb"] = len(out)
            out += thumbnail
        return bytes(out), offsets

    _, offsets = layout({})
    data, final = layout(offsets)
    assert offsets == final
    return data


def exif_payload(tiff: bytes) -> bytes:
    return b"Exif\x00\x00" + tiff
