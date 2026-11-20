"""EXIF / TIFF metadata structures, shared by JPEG, PNG, WebP and TIFF.

EXIF data is a little TIFF file: an 8-byte header (byte order, the magic
number 42, the offset of the first IFD) followed by Image File Directories
(IFDs) of tagged values. All offsets are relative to the start of the TIFF
header, never to the start of the containing file.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

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


# -- IFDs and values ---------------------------------------------------------------

MAX_IFDS = 32
MAX_ENTRIES_PER_IFD = 1000

BYTE, ASCII, SHORT, LONG, RATIONAL, SBYTE, UNDEFINED, SSHORT, SLONG, SRATIONAL, FLOAT, DOUBLE = range(1, 13)
IFD_TYPE = 13  # TIFF-EP: a LONG that points to an IFD

TYPE_SIZES = {
    BYTE: 1, ASCII: 1, SHORT: 2, LONG: 4, RATIONAL: 8, SBYTE: 1, UNDEFINED: 1,
    SSHORT: 2, SLONG: 4, SRATIONAL: 8, FLOAT: 4, DOUBLE: 8, IFD_TYPE: 4,
}
TYPE_NAMES = {
    BYTE: "BYTE", ASCII: "ASCII", SHORT: "SHORT", LONG: "LONG", RATIONAL: "RATIONAL",
    SBYTE: "SBYTE", UNDEFINED: "UNDEFINED", SSHORT: "SSHORT", SLONG: "SLONG",
    SRATIONAL: "SRATIONAL", FLOAT: "FLOAT", DOUBLE: "DOUBLE", IFD_TYPE: "IFD",
}
_STRUCT_CODES = {BYTE: "B", SBYTE: "b", SHORT: "H", LONG: "I", SSHORT: "h", SLONG: "i",
                 FLOAT: "f", DOUBLE: "d", IFD_TYPE: "I", RATIONAL: "I", SRATIONAL: "i"}

EXIF_IFD_POINTER = 0x8769
GPS_IFD_POINTER = 0x8825
INTEROP_IFD_POINTER = 0xA005
DEFAULT_SUB_IFDS = {EXIF_IFD_POINTER: "Exif", GPS_IFD_POINTER: "GPS",
                    INTEROP_IFD_POINTER: "Interop"}


@dataclass(frozen=True)
class Rational:
    """A TIFF RATIONAL/SRATIONAL. A zero denominator is kept, not an error."""

    numerator: int
    denominator: int

    def as_float(self) -> float | None:
        if self.denominator == 0:
            return None
        return self.numerator / self.denominator

    def __str__(self) -> str:
        return f"{self.numerator}/{self.denominator}"


@dataclass
class IfdEntry:
    tag: int
    type: int
    count: int
    value: Any = None
    """Decoded value: a scalar when count is 1, else a tuple (bytes for
    UNDEFINED, str for ASCII). None if it could not be read."""
    raw: bytes = b""
    """The value's raw bytes."""
    value_offset: int | None = None
    """Offset of an out-of-line value (relative to the TIFF header), None if inline."""
    error: str | None = None

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.type, f"unknown type {self.type}")


@dataclass
class Ifd:
    name: str
    offset: int
    entries: list[IfdEntry] = field(default_factory=list)
    next_offset: int = 0

    def get(self, tag: int) -> IfdEntry | None:
        for entry in self.entries:
            if entry.tag == tag:
                return entry
        return None

    def value(self, tag: int) -> Any:
        entry = self.get(tag)
        return None if entry is None else entry.value


@dataclass
class TiffStructure:
    header: TiffHeader
    data: bytes
    ifds: list[Ifd] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def ifd(self, name: str) -> Ifd | None:
        for ifd in self.ifds:
            if ifd.name == name:
                return ifd
        return None


def parse_exif(payload: bytes) -> TiffStructure:
    """Parse an APP1 "Exif\\0\\0" payload."""
    return parse_tiff(tiff_from_exif_payload(payload))


def parse_tiff(tiff: bytes, sub_ifd_tags: dict[int, str] | None = None) -> TiffStructure:
    """Read IFD0, the chain of IFDs after it (IFD1, ...) and sub-IFDs.

    Only a bad TIFF header raises MalformedFileError. Unreadable IFDs and
    values are reported in ``warnings`` and skipped. Loops (an IFD pointing
    back to one already read) are detected, and at most MAX_IFDS IFDs and
    MAX_ENTRIES_PER_IFD entries per IFD are read.
    """
    if sub_ifd_tags is None:
        sub_ifd_tags = DEFAULT_SUB_IFDS
    tiff = bytes(tiff)
    header = parse_tiff_header(tiff)
    reader = ByteReader(tiff, header.byte_order)
    result = TiffStructure(header, tiff)
    visited: set[int] = set()
    # (name, offset, position in the main IFD chain or None for sub-IFDs)
    queue: list[tuple[str, int, int | None]] = [("IFD0", header.ifd0_offset, 0)]

    while queue:
        name, offset, chain_index = queue.pop(0)
        if len(result.ifds) >= MAX_IFDS:
            result.warnings.append(f"more than {MAX_IFDS} IFDs; the rest were not read")
            break
        if offset in visited:
            result.warnings.append(
                f"IFD loop detected: {name} points to offset {offset}, which was "
                "already read; not followed")
            continue
        visited.add(offset)
        try:
            ifd = _read_ifd(reader, _unique_name(result, name), offset, result.warnings)
        except MalformedFileError as exc:
            result.warnings.append(f"{name} at offset {offset} could not be read: {exc}")
            continue
        result.ifds.append(ifd)

        for entry in ifd.entries:
            if entry.tag in sub_ifd_tags and entry.error is None:
                target = sub_ifd_tags[entry.tag]
                if entry.type in (LONG, IFD_TYPE) and isinstance(entry.value, int):
                    if entry.value:
                        queue.append((target, entry.value, None))
                else:
                    result.warnings.append(
                        f"{ifd.name}: invalid {target} IFD pointer ({entry.type_name}, "
                        f"count {entry.count}); not followed")
        if chain_index is not None and ifd.next_offset:
            queue.append((f"IFD{chain_index + 1}", ifd.next_offset, chain_index + 1))
    return result


def _unique_name(result: TiffStructure, name: str) -> str:
    if result.ifd(name) is None:
        return name
    n = 2
    while result.ifd(f"{name} ({n})") is not None:
        n += 1
    return f"{name} ({n})"


def _read_ifd(reader: ByteReader, name: str, offset: int, warnings: list[str]) -> Ifd:
    reader.seek(offset)
    count = reader.u16(f"{name} entry count")
    to_read = count
    if count > MAX_ENTRIES_PER_IFD:
        warnings.append(
            f"{name} claims {count} entries; only the first {MAX_ENTRIES_PER_IFD} were read")
        to_read = MAX_ENTRIES_PER_IFD
    ifd = Ifd(name, offset)
    for i in range(to_read):
        try:
            entry_reader = reader.sub(offset + 2 + 12 * i, 12, f"{name} entry {i}")
        except MalformedFileError:
            warnings.append(f"{name} is truncated: only {i} of {count} entries present")
            return ifd
        entry = _read_entry(reader, entry_reader)
        if entry.error:
            warnings.append(f"{name} tag 0x{entry.tag:04X}: {entry.error}")
        ifd.entries.append(entry)
    try:
        next_reader = reader.sub(offset + 2 + 12 * count, 4, f"{name} next-IFD offset")
        ifd.next_offset = next_reader.u32()
    except MalformedFileError:
        warnings.append(f"{name} has no next-IFD offset (truncated)")
    return ifd


def _read_entry(tiff: ByteReader, er: ByteReader) -> IfdEntry:
    tag = er.u16("tag")
    typ = er.u16("type")
    count = er.u32("count")
    entry = IfdEntry(tag, typ, count)
    if typ not in TYPE_SIZES:
        entry.error = f"unknown value type {typ}; value not read"
        return entry
    # Python ints don't overflow, but count * size can still be absurdly
    # large (up to 2**32 * 8); the bounds check below rejects it before any
    # allocation happens.
    size = TYPE_SIZES[typ] * count
    if size <= 4:
        entry.raw = er.read(size, "inline value")
    else:
        offset = er.u32("value offset")
        entry.value_offset = offset
        if offset > len(tiff) or size > len(tiff) - offset:
            entry.error = (f"value of {size} bytes at offset {offset} is outside the "
                           f"EXIF data ({len(tiff)} bytes)")
            return entry
        entry.raw = tiff.sub(offset, size, "value").read(size)
    entry.value = decode_value(typ, count, entry.raw, tiff.endian)
    return entry


def decode_value(typ: int, count: int, raw: bytes, byte_order: str) -> Any:
    """Decode the raw bytes of a value; scalars for count 1, tuples otherwise."""
    if typ == ASCII:
        return decode_ascii(raw)
    if typ == UNDEFINED:
        return raw
    prefix = "<" if byte_order == "little" else ">"
    code = _STRUCT_CODES[typ]
    if typ in (RATIONAL, SRATIONAL):
        nums = struct.unpack(prefix + code * (2 * count), raw)
        values: tuple = tuple(Rational(nums[i], nums[i + 1]) for i in range(0, len(nums), 2))
    else:
        values = struct.unpack(prefix + code * count, raw)
    if count == 1:
        return values[0]
    return values


def decode_ascii(raw: bytes) -> str:
    """TIFF ASCII: drop NULs (terminators and separators), decode leniently."""
    parts = [p for p in raw.split(b"\x00") if p]
    return " / ".join(p.decode("utf-8", errors="replace") for p in parts).rstrip()
