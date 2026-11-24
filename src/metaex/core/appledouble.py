"""macOS extended attributes: AppleDouble files and the values they hold.

When macOS puts a file in a ZIP or TAR archive (Finder's "Compress",
``ditto``, ``tar``), it adds an AppleDouble file next to it: ``._photo.jpg``
(in a ZIP, usually under ``__MACOSX/``). It holds the file's extended
attributes, which can reveal where and when it was downloaded:

- ``com.apple.quarantine``: "flags;hex time;agent;event ID", e.g. the
  browser that downloaded the file and when;
- ``com.apple.metadata:kMDItemWhereFroms``: a binary property list with the
  URL the file was downloaded from and the page that linked to it;
- ``com.apple.metadata:kMDItemDownloadedDate``, tags, Finder info and the
  resource fork.

Everything here is bounded and read with bounds-checked readers; damaged
data becomes a short description, never an exception.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from metaex.core.errors import MalformedFileError
from metaex.core.reader import ByteReader

APPLEDOUBLE_MAGIC = 0x00051607
APPLESINGLE_MAGIC = 0x00051600
ATTR_MAGIC = b"ATTR"
MAX_ENTRIES = 64
MAX_ATTRS = 1000
MAX_SHOWN = 512
"""Characters of an attribute value shown at most."""

QUARANTINE = "com.apple.quarantine"
WHERE_FROMS = "com.apple.metadata:kMDItemWhereFroms"
DOWNLOADED_DATE = "com.apple.metadata:kMDItemDownloadedDate"

ENTRY_NAMES = {1: "data fork", 2: "resource fork", 3: "original file name", 4: "comment",
               5: "icon", 6: "color icon", 8: "file dates", 9: "Finder info",
               10: "Macintosh file info", 11: "ProDOS file info", 12: "MS-DOS file info",
               13: "AFP short name", 14: "AFP file info", 15: "AFP directory ID"}


@dataclass
class AppleDouble:
    xattrs: list[tuple[str, bytes]] = field(default_factory=list)
    """Extended attributes, in file order (Finder info and the resource fork
    are listed as com.apple.FinderInfo and com.apple.ResourceFork)."""
    other: list[tuple[str, str]] = field(default_factory=list)
    """Other AppleDouble entries: (name, description)."""
    error: str | None = None


def is_appledouble(data: bytes) -> bool:
    return len(data) >= 26 and int.from_bytes(data[:4], "big") in (APPLEDOUBLE_MAGIC,
                                                                     APPLESINGLE_MAGIC)


def parse_appledouble(data: bytes) -> AppleDouble:
    """The attributes stored in an AppleDouble (or AppleSingle) file. A
    damaged file gives what could be read, with ``error`` set."""
    out = AppleDouble()
    try:
        _parse(bytes(data), out)
    except MalformedFileError as exc:
        out.error = str(exc)
    return out


def _parse(data: bytes, out: AppleDouble) -> None:
    r = ByteReader(data, "big")
    if r.u32("AppleDouble magic") not in (APPLEDOUBLE_MAGIC, APPLESINGLE_MAGIC):
        raise MalformedFileError("not an AppleDouble file")
    r.skip(4 + 16, "AppleDouble version and filler")
    count = r.u16("AppleDouble entry count")
    if count > MAX_ENTRIES:
        raise MalformedFileError(f"AppleDouble file has {count} entries")
    for _ in range(count):
        entry_id, offset, length = r.u32("entry ID"), r.u32("entry offset"), r.u32("entry length")
        body = ByteReader(data, "big").sub(offset, length, "AppleDouble entry").read(length)
        if entry_id == 9:
            if body[:32].strip(b"\x00"):
                out.xattrs.append(("com.apple.FinderInfo", body[:32]))
            if len(body) > 34 + 36 and body[34:38] == ATTR_MAGIC:
                _attributes(data, offset + 34, out)
        elif entry_id == 2:
            if length:
                out.xattrs.append(("com.apple.ResourceFork", body))
        elif entry_id == 1:
            continue  # AppleSingle: the file's contents
        else:
            name = ENTRY_NAMES.get(entry_id, f"entry {entry_id}")
            out.other.append((name, _entry_text(entry_id, body)))


def _attributes(data: bytes, start: int, out: AppleDouble) -> None:
    r = ByteReader(data, "big")
    r.seek(start + 4 + 4 + 4 + 4 + 4 + 12)  # magic, debug tag, sizes, reserved
    r.skip(2, "attribute flags")
    count = r.u16("attribute count")
    if count > MAX_ATTRS:
        raise MalformedFileError(f"AppleDouble file has {count} attributes")
    for _ in range(count):
        offset, length = r.u32("attribute offset"), r.u32("attribute length")
        r.skip(2, "attribute flags")
        name_len = r.u8("attribute name length")
        raw = r.read(name_len, "attribute name")
        name = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        value = ByteReader(data, "big").sub(offset, length, f"attribute {name}").read(length)
        out.xattrs.append((name, value))
        r.seek((r.tell() + 3) & ~3)  # entries are aligned to 4 bytes


_MAC_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _entry_text(entry_id: int, body: bytes) -> str:
    if entry_id in (3, 4):
        return _shorten(body.decode("mac_roman", errors="replace"))
    if entry_id == 8 and len(body) >= 16:
        names = ("created", "modified", "backed up", "accessed")
        parts = []
        for i, n in enumerate(names):
            value = struct.unpack(">i", body[4 * i:4 * i + 4])[0]
            if value != -0x80000000:  # "unknown"
                parts.append(f"{n} {(_MAC_EPOCH + timedelta(seconds=value)).isoformat()}")
        return ", ".join(parts) or "no dates"
    return f"{len(body)} bytes"


# -- attribute values ------------------------------------------------------------------


def describe(name: str, value: bytes) -> str:
    """An attribute's value for people to read."""
    try:
        if name == QUARANTINE:
            return quarantine_text(value)
        if name == "com.apple.ResourceFork":
            return f"resource fork, {len(value)} bytes"
        if name == "com.apple.FinderInfo":
            return finder_info_text(value)
        if value.startswith(b"bplist00"):
            return _shorten(plist_text(parse_bplist(value)))
    except MalformedFileError as exc:
        return f"damaged ({exc}; {len(value)} bytes)"
    return _shorten(bytes_text(value))


def quarantine_text(value: bytes) -> str:
    """"0083;65f1a2b3;Safari;UUID" -> "downloaded with Safari on ...; event UUID"."""
    text = value.decode("utf-8", errors="replace").rstrip("\x00")
    parts = text.split(";")
    if len(parts) < 2:
        return _shorten(text)
    out = []
    try:
        when = datetime.fromtimestamp(int(parts[1], 16), tz=timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        when = None
    agent = parts[2] if len(parts) > 2 and parts[2] else None
    if agent:
        out.append(f"downloaded with {agent}")
    if when:
        out.append(f"on {when}")
    if len(parts) > 3 and parts[3]:
        out.append(f"(event {parts[3]})")
    return _shorten(" ".join(out) + f" [{text}]" if out else text)


def finder_info_text(value: bytes) -> str:
    kind, creator = value[:4], value[4:8]
    parts = []
    if kind.strip(b"\x00"):
        parts.append(f"type {kind.decode('mac_roman', errors='replace')!r}")
    if creator.strip(b"\x00"):
        parts.append(f"creator {creator.decode('mac_roman', errors='replace')!r}")
    if len(value) >= 10 and value[8:10] != b"\x00\x00":
        flags = int.from_bytes(value[8:10], "big")
        color = (flags >> 1) & 7
        parts.append(f"Finder flags 0x{flags:04x}" + (f" (color label {color})" if color else ""))
    return ", ".join(parts) or f"{len(value)} bytes"


def bytes_text(value: bytes) -> str:
    text = value.rstrip(b"\x00")
    try:
        decoded = text.decode("utf-8")
    except UnicodeDecodeError:
        return f"{len(value)} bytes of binary data"
    if any(ord(c) < 0x20 and c not in "\t\n\r" for c in decoded):
        return f"{len(value)} bytes of binary data"
    return decoded


def _shorten(text: str) -> str:
    return text if len(text) <= MAX_SHOWN else text[:MAX_SHOWN - 3] + "..."


# -- binary property lists ---------------------------------------------------------------

MAX_OBJECTS = 10_000
MAX_DEPTH = 16
MAX_VISITS = 50_000
"""Objects read at most (one object can be used many times)."""
_CF_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def parse_bplist(data: bytes):
    """The top object of a binary property list ("bplist00"): None, bool,
    int, float, datetime, bytes, str, list or dict. Bounded: at most
    MAX_OBJECTS objects, MAX_DEPTH levels, no object used inside itself.
    Raises MalformedFileError."""
    data = bytes(data)
    if not data.startswith(b"bplist00") or len(data) < 8 + 32:
        raise MalformedFileError("not a binary property list")
    trailer = ByteReader(data, "big").sub(len(data) - 32, 32, "property list trailer")
    trailer.skip(6)
    offset_size, ref_size = trailer.u8(), trailer.u8()
    count, top, table = trailer.u64(), trailer.u64(), trailer.u64()
    if offset_size not in (1, 2, 4, 8) or ref_size not in (1, 2, 4, 8):
        raise MalformedFileError("property list has invalid integer sizes")
    if count > MAX_OBJECTS or top >= count:
        raise MalformedFileError("property list has too many objects or a bad top object")
    r = ByteReader(data, "big")
    offsets_reader = r.sub(table, count * offset_size, "property list offset table")
    offsets = [int.from_bytes(offsets_reader.read(offset_size), "big") for _ in range(count)]
    limit = len(data) - 32
    visits = 0

    def obj(index: int, depth: int, path: frozenset):
        nonlocal visits
        visits += 1
        if visits > MAX_VISITS:
            raise MalformedFileError("property list refers to its objects too many times")
        if index >= count or index in path:
            raise MalformedFileError("property list refers to a missing or enclosing object")
        if depth > MAX_DEPTH:
            raise MalformedFileError("property list is nested too deeply")
        pos = offsets[index]
        if not 8 <= pos < limit:
            raise MalformedFileError("property list object is outside the data")
        o = r.sub(pos, limit - pos, "property list object")
        marker = o.u8()
        kind, info = marker >> 4, marker & 0xF
        if kind == 0x0:
            return {0x0: None, 0x8: False, 0x9: True}.get(info)
        if kind == 0x1:
            return int.from_bytes(o.read(1 << info), "big", signed=info >= 3)
        if kind == 0x2:
            raw = o.read(1 << info)
            return struct.unpack(">d" if len(raw) == 8 else ">f", raw)[0] if len(raw) in (4, 8) \
                else None
        if kind == 0x3:
            seconds = struct.unpack(">d", o.read(8))[0]
            try:
                return _CF_EPOCH + timedelta(seconds=seconds)
            except (OverflowError, ValueError):
                return f"invalid date ({seconds})"
        size = info
        if info == 0xF:
            m = o.u8()
            if m >> 4 != 0x1:
                raise MalformedFileError("property list has a bad length")
            size = int.from_bytes(o.read(1 << (m & 0xF)), "big")
        if kind == 0x4:
            return o.read(size)
        if kind == 0x5:
            return o.read(size).decode("ascii", errors="replace")
        if kind == 0x6:
            return o.read(2 * size).decode("utf-16-be", errors="replace")
        if kind == 0x8:
            return int.from_bytes(o.read(info + 1), "big")
        if kind in (0xA, 0xC, 0xD):
            n = size * (2 if kind == 0xD else 1)
            if n > count:
                raise MalformedFileError("property list container is larger than the list")
            refs = [int.from_bytes(o.read(ref_size), "big") for _ in range(n)]
            inner = path | {index}
            values = [obj(i, depth + 1, inner) for i in refs]
            if kind == 0xD:
                return {str(k): v for k, v in zip(values[:size], values[size:])}
            return values
        raise MalformedFileError(f"property list has an unknown object type 0x{kind:x}")

    return obj(top, 0, frozenset())


def plist_text(value) -> str:
    if isinstance(value, list):
        return ", ".join(plist_text(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {plist_text(v)}" for k, v in value.items())
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, bytes):
        return f"{len(value)} bytes"
    return str(value)
