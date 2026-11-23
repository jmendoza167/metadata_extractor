"""Build ZIP archives byte by byte, including broken and hostile ones.

The stdlib zipfile module can't write most of what the tests need (bad
names, mismatched headers, ZIP64 for small files, hidden data), so this
helper writes every record itself. Archives made only of STORED entries
with fixed times are byte-identical on every machine (the samples use
them).
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

STORED, DEFLATED = 0, 8
NO_DATE, NO_TIME = 0x0021, 0x0000  # 1980-01-01 00:00:00
FLAG_ENCRYPTED, FLAG_DESCRIPTOR, FLAG_UTF8 = 0x0001, 0x0008, 0x0800


def deflate_raw(data: bytes, level: int = 9) -> bytes:
    c = zlib.compressobj(level, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def extra(header_id: int, data: bytes) -> bytes:
    return struct.pack("<HH", header_id, len(data)) + data


def ntfs_times(modified: int, accessed: int, created: int) -> bytes:
    """An NTFS extra field (times are FILETIME: 100 ns since 1601)."""
    return extra(0x000A, b"\x00" * 4 + struct.pack("<HHQQQ", 1, 24, modified, accessed, created))


def unix_times(modified: int) -> bytes:
    return extra(0x5455, struct.pack("<Bi", 1, modified))


def unix_ids(uid: int = 1000, gid: int = 1000) -> bytes:
    return extra(0x7875, struct.pack("<BBIBI", 1, 4, uid, 4, gid))


def dos_stamp(year: int, month: int, day: int, hour: int = 0, minute: int = 0,
              second: int = 0) -> tuple[int, int]:
    """(DOS date, DOS time)."""
    return ((year - 1980) << 9) | (month << 5) | day, (hour << 11) | (minute << 5) | (second // 2)


@dataclass
class Entry:
    name: str | bytes
    data: bytes = b""
    method: int = STORED
    compressed: bytes | None = None
    """The bytes stored for the entry; default: ``data``, compressed with ``method``."""
    flags: int | None = None
    """Default: the UTF-8 flag if the name is not ASCII."""
    crc: int | None = None
    size: int | None = None
    """Declared uncompressed size (default: len(data))."""
    csize: int | None = None
    """Declared compressed size (default: the real one)."""
    date: int = NO_DATE
    time: int = NO_TIME
    created_by: int = 20
    version_needed: int = 20
    external_attr: int = 0
    local_extra: bytes = b""
    central_extra: bytes = b""
    comment: bytes = b""
    descriptor: bool = False
    """Write a data descriptor (and set its flag)."""
    descriptor_signature: bool = True
    zip64: bool = False
    """Write the sizes and offset in ZIP64 extra fields."""
    local: dict = field(default_factory=dict)
    """Overrides for the local header: name, flags, method, crc, size, csize."""
    offset: int | None = None
    """Override the local header offset written in the central directory."""

    def payload(self) -> bytes:
        if self.compressed is not None:
            return self.compressed
        return deflate_raw(self.data) if self.method == DEFLATED else self.data


def _name(name: str | bytes) -> bytes:
    return name if isinstance(name, bytes) else name.encode("utf-8")


def local_header(e: Entry, payload: bytes) -> bytes:
    name = _name(e.local.get("name", e.name))
    flags = e.local.get("flags", _flags(e))
    method = e.local.get("method", e.method)
    crc = e.local.get("crc", _crc(e))
    size = e.local.get("size", _size(e))
    csize = e.local.get("csize", e.csize if e.csize is not None else len(payload))
    extra_field = e.local_extra
    if e.descriptor:
        crc = size = csize = 0
    if e.zip64:
        extra_field = extra(0x0001, struct.pack("<QQ", size, csize)) + extra_field
        size = csize = 0xFFFFFFFF
    return (b"PK\x03\x04" + struct.pack("<HHHHHIIIHH", e.version_needed, flags, method, e.time,
                                        e.date, crc, csize, size, len(name), len(extra_field))
            + name + extra_field)


def _flags(e: Entry) -> int:
    if e.flags is not None:
        flags = e.flags
    else:
        flags = 0 if isinstance(e.name, bytes) or e.name.isascii() else FLAG_UTF8
    return flags | (FLAG_DESCRIPTOR if e.descriptor else 0)


def _crc(e: Entry) -> int:
    return e.crc if e.crc is not None else zlib.crc32(e.data)


def _size(e: Entry) -> int:
    return e.size if e.size is not None else len(e.data)


def descriptor(e: Entry, payload: bytes) -> bytes:
    sig = b"PK\x07\x08" if e.descriptor_signature else b""
    fmt = "<IQQ" if e.zip64 else "<III"
    return sig + struct.pack(fmt, _crc(e), e.csize if e.csize is not None else len(payload),
                             _size(e))


def central_header(e: Entry, payload: bytes, offset: int) -> bytes:
    name = _name(e.name)
    size, csize = _size(e), (e.csize if e.csize is not None else len(payload))
    extra_field = e.central_extra
    if e.zip64:
        extra_field = extra(0x0001, struct.pack("<QQQ", size, csize, offset)) + extra_field
        size = csize = offset = 0xFFFFFFFF
    return (b"PK\x01\x02" + struct.pack("<HHHHHHIIIHHHHHII", e.created_by, e.version_needed,
                                        _flags(e), e.method, e.time, e.date, _crc(e), csize,
                                        size, len(name), len(extra_field), len(e.comment), 0, 0,
                                        e.external_attr, offset)
            + name + extra_field + e.comment)


def build_zip(entries: list[Entry], *, comment: bytes = b"", prefix: bytes = b"",
              between: dict[int, bytes] | None = None, before_directory: bytes = b"",
              suffix: bytes = b"", zip64: bool = False, fix_prefix_offsets: bool = True,
              disk: int = 0, total: int | None = None, directory_tail: bytes = b"") -> bytes:
    """An archive of ``entries`` in order.

    ``prefix`` goes before the first entry (offsets account for it unless
    ``fix_prefix_offsets`` is false, like data glued in front of a finished
    archive); ``between[i]`` goes after entry i; ``before_directory`` between
    the last entry and the central directory; ``suffix`` after the end
    record. ``zip64`` writes ZIP64 end records.
    """
    between = between or {}
    out = bytearray(prefix)
    base = 0 if fix_prefix_offsets else len(prefix)
    offsets = []
    for i, e in enumerate(entries):
        payload = e.payload()
        offsets.append(len(out) - base if e.offset is None else e.offset)
        out += local_header(e, payload) + payload
        if e.descriptor:
            out += descriptor(e, payload)
        out += between.get(i, b"")
    out += before_directory
    cd_offset = len(out) - base
    directory = b"".join(central_header(e, e.payload(), off)
                         for e, off in zip(entries, offsets)) + directory_tail
    out += directory
    count = len(entries) if total is None else total
    if zip64:
        z64_offset = len(out) - base
        out += (b"PK\x06\x06" + struct.pack("<QHHIIQQQQ", 44, 45, 45, disk, disk, count, count,
                                            len(directory), cd_offset))
        out += b"PK\x06\x07" + struct.pack("<IQI", 0, z64_offset, 1)
        out += b"PK\x05\x06" + struct.pack("<HHHHIIH", 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF,
                                           0xFFFFFFFF, 0xFFFFFFFF, len(comment))
    else:
        out += b"PK\x05\x06" + struct.pack("<HHHHIIH", disk, disk, count, count,
                                           len(directory), cd_offset, len(comment))
    out += comment + suffix
    return bytes(out)


def make_zip(files: dict[str, bytes | str], method: int = STORED) -> bytes:
    """A plain archive of ``files`` (text is UTF-8 encoded), in order."""
    return build_zip([Entry(name, data.encode("utf-8") if isinstance(data, str) else data,
                            method=method) for name, data in files.items()])
