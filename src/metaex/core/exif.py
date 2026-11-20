"""EXIF / TIFF metadata structures, shared by JPEG, PNG, WebP and TIFF.

EXIF data is a little TIFF file: an 8-byte header (byte order, the magic
number 42, the offset of the first IFD) followed by Image File Directories
(IFDs) of tagged values. All offsets are relative to the start of the TIFF
header, never to the start of the containing file.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from metaex.core import exif_tags
from metaex.core.errors import MalformedFileError
from metaex.core.reader import ByteReader
from metaex.core.result import Section

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


# -- Orientation-only EXIF (written by strip) -------------------------------------

ORIENTATION_TAG = 0x0112
MINIMAL_ORIENTATION_TIFF_SIZE = 8 + 2 + 12 + 4  # header, one-entry IFD0, next-IFD offset


def orientation(st: TiffStructure) -> int | None:
    """The Orientation tag (1-8) from IFD0, or None if missing or invalid."""
    ifd0 = st.ifd("IFD0")
    value = ifd0.value(ORIENTATION_TAG) if ifd0 is not None else None
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 8:
        return value
    return None


def minimal_orientation_tiff(value: int) -> bytes:
    """New TIFF data with only IFD0 holding the Orientation tag (26 bytes, big-endian)."""
    if not 1 <= value <= 8:
        raise ValueError(f"invalid orientation {value}")
    return (b"MM\x00\x2a" + (8).to_bytes(4, "big")
            + (1).to_bytes(2, "big")
            + struct.pack(">HHIHH", ORIENTATION_TAG, SHORT, 1, value, 0)
            + (0).to_bytes(4, "big"))


def is_orientation_only_tiff(tiff: bytes) -> bool:
    """TIFF data holding only IFD0 with the Orientation tag, and no unused bytes."""
    try:
        st = parse_tiff(tiff)
    except MalformedFileError:
        return False
    if [ifd.name for ifd in st.ifds] != ["IFD0"] or st.warnings:
        return False
    ifd0 = st.ifds[0]
    return ([e.tag for e in ifd0.entries] == [ORIENTATION_TAG]
            and ifd0.next_offset == 0
            and st.header.ifd0_offset == 8
            and len(tiff) == MINIMAL_ORIENTATION_TIFF_SIZE)


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


# -- names, formatting and report sections ------------------------------------------

# IFDs whose tags are shown without an IFD prefix.
_PLAIN_IFDS = {"IFD0", "Exif", "Interop"}
_DATETIME_OFFSET_TAGS = {
    "DateTime": "OffsetTime",
    "DateTimeOriginal": "OffsetTimeOriginal",
    "DateTimeDigitized": "OffsetTimeDigitized",
}
_VERSION_TAGS = {"ExifVersion", "FlashpixVersion", "InteroperabilityVersion"}
_OFFSET_RE = re.compile(r"[+-](\d\d):(\d\d)")


def base_ifd_name(name: str) -> str:
    """'Exif (2)' -> 'Exif'."""
    return name.split(" (", 1)[0]


def tag_name(ifd_name: str, tag: int) -> str | None:
    base = base_ifd_name(ifd_name)
    if base == "Interop":
        return exif_tags.INTEROP_TAGS.get(tag)
    if base == "GPS":
        return exif_tags.GPS_TAGS.get(tag)
    return exif_tags.MAIN_TAGS.get(tag)


def exif_sections(st: TiffStructure, warnings: list[str] | None = None,
                  image_size: Callable[[bytes], tuple[int, int] | None] | None = None,
                  ) -> list[Section]:
    """Report sections for parsed EXIF data. Every tag is listed, known or not.

    Problems found while interpreting values (e.g. invalid GPS coordinates)
    and critical findings are appended to ``warnings`` if given.
    ``image_size`` returns (width, height) of an embedded JPEG thumbnail; it
    is passed in so this module doesn't depend on a specific format module.
    """
    if warnings is None:
        warnings = []
    section = Section("exif", "EXIF")
    order = "little-endian (II)" if st.header.byte_order == "little" else "big-endian (MM)"
    section.add("byte_order", "Byte order", order)
    ctx = _context(st)
    gps_ifd = st.ifd("GPS")
    ifd1 = st.ifd("IFD1")
    for ifd in st.ifds:
        if ifd is gps_ifd or ifd is ifd1:
            continue
        for entry in ifd.entries:
            if entry.tag in exif_tags.STRUCTURAL_TAGS:
                continue
            _add_entry(section, ifd, entry, ctx)
    sections = [section]
    if gps_ifd is not None:
        sections.append(gps_section(gps_ifd, ctx, warnings))
    if ifd1 is not None:
        sections.append(thumbnail_section(st, ifd1, ctx, warnings, image_size))
    return sections


def _context(st: TiffStructure) -> dict[str, Any]:
    """Values other tags need for formatting (camera make, time zone offsets)."""
    ctx: dict[str, Any] = {"byte_order": st.header.byte_order, "offsets": {}}
    for ifd in st.ifds:
        if base_ifd_name(ifd.name) not in _PLAIN_IFDS:
            continue
        for entry in ifd.entries:
            name = tag_name(ifd.name, entry.tag)
            if name == "Make" and isinstance(entry.value, str):
                ctx.setdefault("make", entry.value)
            elif name and name.startswith("OffsetTime") and isinstance(entry.value, str):
                ctx["offsets"][name] = entry.value
    return ctx


def _add_entry(section: Section, ifd: Ifd, entry: IfdEntry, ctx: dict[str, Any],
               *, prefix: bool = True, sensitive: bool | None = None) -> None:
    name = tag_name(ifd.name, entry.tag)
    label = name or f"Unknown tag 0x{entry.tag:04X}"
    key = name or f"tag_0x{entry.tag:04x}"
    if prefix and base_ifd_name(ifd.name) not in _PLAIN_IFDS:
        label = f"{ifd.name} {label}"
        key = f"{ifd.name}.{key}"
    if sensitive is None:
        sensitive = name is not None and exif_tags.is_sensitive_name(name)
    if entry.error:
        value, note = "(not readable)", entry.error
    else:
        value, note = format_entry(entry, name, ctx)
    section.add(key, label, value, sensitive=sensitive, note=note)


def format_entry(entry: IfdEntry, name: str | None, ctx: dict[str, Any]) -> tuple[Any, str | None]:
    """Readable value and optional note for one entry."""
    v = entry.value
    if name == "ExposureTime":
        return format_exposure_time(v), None
    if name == "FNumber":
        return format_fnumber(v), None
    if name in ("FocalLength", "FocalLengthIn35mmFilm"):
        return format_length_mm(v), None
    if name == "Flash" and isinstance(v, int):
        return format_flash(v), None
    if name in _DATETIME_OFFSET_TAGS and isinstance(v, str):
        return format_datetime(v, ctx["offsets"].get(_DATETIME_OFFSET_TAGS[name]))
    if name is not None and name.startswith("XP"):
        return decode_xp(entry.raw), None
    if name == "MakerNote":
        make = ctx.get("make") or "unknown"
        return (f"present, {len(entry.raw)} bytes (camera make: {make})",
                "vendor-specific format; contents not decoded")
    if name == "UserComment":
        return decode_user_comment(entry.raw, ctx["byte_order"]), None
    if name in _VERSION_TAGS and isinstance(v, bytes):
        return format_version(v), None
    if name == "ComponentsConfiguration" and isinstance(v, bytes):
        return format_components(v), None
    if name == "GPSVersionID" and isinstance(v, tuple) and len(v) == 4:
        return ".".join(str(b) for b in v), None
    table = exif_tags.ENUMS.get(entry.tag) if exif_tags.MAIN_TAGS.get(entry.tag) == name else None
    if table is not None and isinstance(v, int):
        if v in table:
            return table[v], None
        return v, "unknown value"
    return format_generic(v), None


def format_generic(value: Any) -> Any:
    if isinstance(value, Rational):
        return format_rational(value)
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, bytes):
        if len(value) > 16:
            return value  # rendered as "<N bytes>"
        if value and all(0x20 <= b < 0x7F for b in value):
            return value.decode("ascii")
        return " ".join(f"{b:02X}" for b in value) or "(empty)"
    if isinstance(value, tuple):
        if len(value) > 64:
            return f"{len(value)} values (not shown)"
        return ", ".join(str(format_generic(v)) for v in value) or "(empty)"
    if isinstance(value, str):
        return value or "(empty)"
    return value


def format_rational(r: Rational) -> str:
    value = r.as_float()
    if value is None:
        return f"{r} (invalid: zero denominator)"
    if r.denominator == 1:
        return str(r.numerator)
    return f"{round(value, 6):g}"


def _as_float(value: Any) -> float | None:
    if isinstance(value, Rational):
        return value.as_float()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def format_exposure_time(value: Any) -> Any:
    seconds = _as_float(value)
    if seconds is None or not 0 < seconds < float("inf"):
        return format_generic(value)
    if seconds < 1:
        return f"1/{round(1 / seconds)} s"
    return f"{seconds:g} s"


def format_fnumber(value: Any) -> Any:
    f = _as_float(value)
    if f is None or not 0 < f < float("inf"):
        return format_generic(value)
    return f"f/{f:.1f}"


def format_length_mm(value: Any) -> Any:
    mm = _as_float(value)
    if mm is None or mm != mm:
        return format_generic(value)
    return f"{mm:g} mm"


def format_flash(value: int) -> str:
    parts = ["Fired" if value & 0x01 else "Did not fire"]
    mode = {1: "compulsory flash", 2: "flash suppressed", 3: "auto mode"}.get((value >> 3) & 0x3)
    if mode:
        parts.append(mode)
    returned = {2: "return light not detected", 3: "return light detected"}.get((value >> 1) & 0x3)
    if returned:
        parts.append(returned)
    if value & 0x20:
        parts.append("no flash function")
    if value & 0x40:
        parts.append("red-eye reduction")
    return ", ".join(parts)


def format_datetime(text: str, offset: str | None) -> tuple[str, str | None]:
    """EXIF 'YYYY:MM:DD HH:MM:SS' (plus OffsetTime*) -> ISO 8601."""
    try:
        moment = datetime.strptime(text.strip(), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return text or "(empty)", "not a valid EXIF date"
    iso = moment.isoformat()
    if offset is not None:
        match = _OFFSET_RE.fullmatch(offset.strip())
        if match and int(match.group(1)) <= 14 and int(match.group(2)) < 60:
            return iso + offset.strip(), None
        return iso, f"invalid time zone offset {offset!r}"
    return iso, "no time zone recorded"


def format_version(raw: bytes) -> str:
    """b'0232' -> '2.32'."""
    if len(raw) == 4 and raw.isdigit():
        text = raw.decode("ascii")
        return f"{int(text[:2])}.{text[2:]}"
    return format_generic(raw)


def format_components(raw: bytes) -> str:
    names = {0: "-", 1: "Y", 2: "Cb", 3: "Cr", 4: "R", 5: "G", 6: "B"}
    return ", ".join(names.get(b, f"0x{b:02X}") for b in raw)


def decode_xp(raw: bytes) -> str:
    """Windows XP* tags: UTF-16LE text stored as BYTE."""
    text = raw[: len(raw) - (len(raw) % 2)].decode("utf-16-le", errors="replace")
    return text.replace("\x00", "").strip() or "(empty)"


def decode_user_comment(raw: bytes, byte_order: str) -> str:
    """UserComment / GPSProcessingMethod: 8-byte character code, then text."""
    code, body = raw[:8], raw[8:]
    if code.startswith(b"UNICODE"):
        if body.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = body[: len(body) - (len(body) % 2)].decode("utf-16", errors="replace")
        else:
            encoding = "utf-16-le" if byte_order == "little" else "utf-16-be"
            text = body[: len(body) - (len(body) % 2)].decode(encoding, errors="replace")
    elif code.startswith(b"JIS"):
        text = body.decode("iso2022_jp", errors="replace")
    elif code.startswith(b"ASCII") or code == b"\x00" * 8:
        text = body.decode("utf-8", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    return text.replace("\x00", "").strip() or "(empty)"


# -- Thumbnail (IFD1) -------------------------------------------------------------

JPEG_INTERCHANGE_FORMAT = 0x0201
JPEG_INTERCHANGE_FORMAT_LENGTH = 0x0202
THUMBNAIL_WARNING = "embedded thumbnail may show the original, uncropped image"


@dataclass(frozen=True)
class Thumbnail:
    offset: int
    """Offset of the thumbnail inside the TIFF data."""
    data: bytes


def exif_thumbnail(st: TiffStructure, problems: list[str] | None = None) -> Thumbnail | None:
    """The JPEG thumbnail referenced by IFD1, if present and within bounds."""
    ifd1 = st.ifd("IFD1")
    if ifd1 is None:
        return None
    offset = ifd1.value(JPEG_INTERCHANGE_FORMAT)
    length = ifd1.value(JPEG_INTERCHANGE_FORMAT_LENGTH)
    if offset is None and length is None:
        return None
    if not isinstance(offset, int) or not isinstance(length, int) or length <= 0:
        if problems is not None:
            problems.append("EXIF thumbnail pointer is invalid; thumbnail not read")
        return None
    if offset > len(st.data) or length > len(st.data) - offset:
        if problems is not None:
            problems.append(f"EXIF thumbnail ({length} bytes at offset {offset}) extends "
                            "past the end of the EXIF data; thumbnail not read")
        return None
    return Thumbnail(offset, st.data[offset : offset + length])


def thumbnail_section(st: TiffStructure, ifd1: Ifd, ctx: dict[str, Any], warnings: list[str],
                      image_size: Callable[[bytes], tuple[int, int] | None] | None) -> Section:
    section = Section("thumbnail", "Thumbnail")
    thumb = exif_thumbnail(st, warnings)
    if thumb is not None:
        is_jpeg = thumb.data.startswith(b"\xff\xd8")
        section.add("embedded_thumbnail", "Embedded thumbnail",
                    f"{'JPEG' if is_jpeg else 'unrecognized data'}, {len(thumb.data)} bytes",
                    sensitive=True, note=f"at offset {thumb.offset} in the EXIF data")
        size = image_size(thumb.data) if (image_size is not None and is_jpeg) else None
        if size is not None:
            section.add("dimensions", "Dimensions", f"{size[0]} x {size[1]} pixels")
        warnings.append(THUMBNAIL_WARNING)
    elif ifd1.get(0x0111) is not None:  # StripOffsets: uncompressed thumbnail
        section.add("embedded_thumbnail", "Embedded thumbnail",
                    "uncompressed (strips)", sensitive=True, note="not extracted")
        warnings.append(THUMBNAIL_WARNING)
    for entry in ifd1.entries:
        if entry.tag in exif_tags.STRUCTURAL_TAGS:
            continue
        _add_entry(section, ifd1, entry, ctx, prefix=False)
    return section


# -- GPS ----------------------------------------------------------------------------

GPS_LATITUDE_REF, GPS_LATITUDE, GPS_LONGITUDE_REF, GPS_LONGITUDE = 0x0001, 0x0002, 0x0003, 0x0004
GPS_ALTITUDE_REF, GPS_ALTITUDE, GPS_TIMESTAMP = 0x0005, 0x0006, 0x0007
GPS_SPEED_REF, GPS_SPEED = 0x000C, 0x000D
GPS_IMG_DIRECTION_REF, GPS_IMG_DIRECTION = 0x0010, 0x0011
GPS_PROCESSING_METHOD, GPS_DATESTAMP = 0x001B, 0x001D

MAX_ALTITUDE_M = 100_000.0
MAX_SPEED = 100_000.0


@dataclass(frozen=True)
class Coordinates:
    latitude: float
    longitude: float

    def __str__(self) -> str:
        return f"{self.latitude:.6f}, {self.longitude:.6f}"


def gps_coordinates(gps: Ifd, warnings: list[str] | None = None) -> Coordinates | None:
    """Decimal-degree coordinates, or None if missing or invalid."""
    problems: list[str] = [] if warnings is None else warnings
    lat = _coordinate(gps, GPS_LATITUDE, GPS_LATITUDE_REF, ("N", "S"), 90.0, "latitude", problems)
    lon = _coordinate(gps, GPS_LONGITUDE, GPS_LONGITUDE_REF, ("E", "W"), 180.0, "longitude", problems)
    if lat is None or lon is None:
        return None
    return Coordinates(lat, lon)


def map_links(coords: Coordinates) -> dict[str, str]:
    """Map URLs for the coordinates.

    These are only strings for the user to open if they choose to; metaex
    itself never makes network requests.
    """
    lat, lon = f"{coords.latitude:.6f}", f"{coords.longitude:.6f}"
    return {
        "openstreetmap": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=16/{lat}/{lon}",
        "google_maps": f"https://www.google.com/maps?q={lat},{lon}",
    }


def gps_section(gps: Ifd, ctx: dict[str, Any], warnings: list[str]) -> Section:
    """Interpreted GPS values. Every field is sensitive: this is a location."""
    section = Section("gps", "GPS")
    consumed: set[int] = set()

    coords = gps_coordinates(gps, warnings)
    if coords is not None:
        section.add("coordinates", "Coordinates", str(coords), sensitive=True,
                    note="decimal degrees")
        section.add("latitude", "Latitude", f"{coords.latitude:.6f}", sensitive=True)
        section.add("longitude", "Longitude", f"{coords.longitude:.6f}", sensitive=True)
        links = map_links(coords)
        section.add("openstreetmap", "OpenStreetMap", links["openstreetmap"], sensitive=True)
        section.add("google_maps", "Google Maps", links["google_maps"], sensitive=True)
        consumed |= {GPS_LATITUDE, GPS_LATITUDE_REF, GPS_LONGITUDE, GPS_LONGITUDE_REF}
        warnings.append(f"CRITICAL: GPS coordinates found ({coords}): they reveal "
                        "where the photo was taken")
    elif gps.entries:
        warnings.append("CRITICAL: GPS data found (no valid coordinates)")

    altitude = _gps_altitude(gps, warnings)
    if altitude is not None:
        section.add("altitude", "Altitude", altitude, sensitive=True)
        consumed |= {GPS_ALTITUDE, GPS_ALTITUDE_REF}

    moment = _gps_datetime(gps, warnings)
    if moment is not None:
        section.add("datetime_utc", "Date/time (UTC)", moment, sensitive=True)
        consumed |= {GPS_DATESTAMP, GPS_TIMESTAMP}

    direction = _gps_measure(gps, GPS_IMG_DIRECTION, GPS_IMG_DIRECTION_REF, "image direction",
                             {"T": "true north", "M": "magnetic north"}, "deg", 360.0, warnings)
    if direction is not None:
        section.add("image_direction", "Image direction", direction, sensitive=True)
        consumed |= {GPS_IMG_DIRECTION, GPS_IMG_DIRECTION_REF}

    speed = _gps_measure(gps, GPS_SPEED, GPS_SPEED_REF, "speed",
                         {"K": "km/h", "M": "mph", "N": "knots"}, None, MAX_SPEED, warnings,
                         default_ref="K")
    if speed is not None:
        section.add("speed", "Speed", speed, sensitive=True)
        consumed |= {GPS_SPEED, GPS_SPEED_REF}

    method = gps.get(GPS_PROCESSING_METHOD)
    if method is not None and method.error is None and isinstance(method.value, bytes):
        section.add("processing_method", "Processing method",
                    decode_user_comment(method.raw, ctx["byte_order"]), sensitive=True)
        consumed.add(GPS_PROCESSING_METHOD)

    for entry in gps.entries:
        if entry.tag not in consumed:
            _add_entry(section, gps, entry, ctx, prefix=False, sensitive=True)
    return section


def _floats(value: Any, n: int) -> list[float] | None:
    """n rationals (or one if n == 1) as floats; None if wrong shape or zero denominator."""
    values = (value,) if n == 1 and isinstance(value, Rational) else value
    if not isinstance(values, tuple) or len(values) != n:
        return None
    out = []
    for v in values:
        f = v.as_float() if isinstance(v, Rational) else None
        if f is None:
            return None
        out.append(f)
    return out


def _ref(gps: Ifd, tag: int) -> str | None:
    value = gps.value(tag)
    return value.strip().upper() if isinstance(value, str) else None


def _coordinate(gps: Ifd, value_tag: int, ref_tag: int, refs: tuple[str, str],
                limit: float, what: str, warnings: list[str]) -> float | None:
    entry = gps.get(value_tag)
    if entry is None or entry.error:
        return None
    parts = _floats(entry.value, 3)
    ref = _ref(gps, ref_tag)
    problems = []
    if parts is None:
        problems.append("expected 3 rationals (degrees, minutes, seconds) with "
                        "non-zero denominators")
    else:
        d, m, s = parts
        if not (0 <= d <= limit and 0 <= m < 60 and 0 <= s < 60):
            problems.append(f"out of range: {d:g} deg {m:g} min {s:g} sec")
        elif d + m / 60 + s / 3600 > limit:
            problems.append(f"more than {limit:g} degrees")
    if ref not in refs:
        problems.append(f"reference is {ref!r}, expected {refs[0]} or {refs[1]}")
    if problems:
        warnings.append(f"GPS {what} is invalid ({'; '.join(problems)}); shown raw")
        return None
    d, m, s = parts
    degrees = d + m / 60 + s / 3600
    return -degrees if ref == refs[1] else degrees


def _gps_altitude(gps: Ifd, warnings: list[str]) -> str | None:
    entry = gps.get(GPS_ALTITUDE)
    if entry is None or entry.error:
        return None
    parts = _floats(entry.value, 1)
    ref = gps.value(GPS_ALTITUDE_REF)
    if isinstance(ref, bytes) and len(ref) == 1:
        ref = ref[0]
    if parts is None or not 0 <= parts[0] <= MAX_ALTITUDE_M or ref not in (None, 0, 1):
        warnings.append("GPS altitude is invalid; shown raw")
        return None
    where = "below sea level" if ref == 1 else "above sea level"
    text = f"{parts[0]:.1f} m {where}"
    return text if ref is not None else text + " (reference missing; assumed above)"


def _gps_datetime(gps: Ifd, warnings: list[str]) -> str | None:
    date_entry, time_entry = gps.get(GPS_DATESTAMP), gps.get(GPS_TIMESTAMP)
    if time_entry is None or time_entry.error:
        return None
    parts = _floats(time_entry.value, 3)
    if parts is None or not (0 <= parts[0] < 24 and 0 <= parts[1] < 60 and 0 <= parts[2] < 61):
        warnings.append("GPS time stamp is invalid; shown raw")
        return None
    h, m, s = parts
    clock = f"{int(h):02d}:{int(m):02d}:{s:06.3f}".rstrip("0").rstrip(".")
    if len(clock) < 8:
        clock = f"{int(h):02d}:{int(m):02d}:{int(s):02d}"
    if date_entry is None or date_entry.error or not isinstance(date_entry.value, str):
        return f"{clock} UTC (no date recorded)"
    try:
        day = datetime.strptime(date_entry.value.strip(), "%Y:%m:%d").date()
    except ValueError:
        warnings.append("GPS date stamp is invalid; shown raw")
        return None
    return f"{day.isoformat()}T{clock}Z"


def _gps_measure(gps: Ifd, value_tag: int, ref_tag: int, what: str, refs: dict[str, str],
                 unit: str | None, limit: float, warnings: list[str],
                 default_ref: str | None = None) -> str | None:
    entry = gps.get(value_tag)
    if entry is None or entry.error:
        return None
    parts = _floats(entry.value, 1)
    ref = _ref(gps, ref_tag) or default_ref
    if parts is None or not 0 <= parts[0] <= limit or (ref is not None and ref not in refs):
        warnings.append(f"GPS {what} is invalid; shown raw")
        return None
    unit_text = unit if unit is not None else refs.get(ref or "", "")
    text = f"{parts[0]:g} {unit_text}".strip()
    if unit is not None and ref is not None:
        text += f" ({refs[ref]})"
    return text
