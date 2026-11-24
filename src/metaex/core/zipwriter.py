"""Write ZIP archives that reveal nothing about when, where or by what they
were made.

Every entry gets the same headers: time 1980-01-01 00:00:00 (the DOS
epoch, what Microsoft Office writes too), "made by" MS-DOS / ZIP 2.0, no
extra fields, no comments, no data descriptors, and no archive comment.
An entry given a Unix ``mode`` (plain archives, which keep the executable
bit) is "made by" Unix instead, with that mode in its external attributes
(and the MS-DOS folder attribute for folders), the way Info-ZIP and
Python's zipfile write them.
Entries are written in the order given, except that the names passed as
``first`` move to the front ([Content_Types].xml for OOXML, mimetype for
OpenDocument, which must also be stored uncompressed).

Entries copied from an existing archive keep their compressed bytes
exactly (they are not decompressed and compressed again); only the
headers around them are new.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterable
from dataclasses import dataclass

from metaex.core.ziparchive import (DEFLATED, NO_TIME, STORED, ZipArchive, ZipEntry)

VERSION = 20
"""ZIP version 2.0, operating system 0 (MS-DOS): "made by" and "needed"."""
UNIX_VERSION = 3 << 8 | VERSION
"""ZIP version 2.0, operating system 3 (Unix): "made by" of entries with a mode."""
DOS_DIRECTORY = 0x10
FLAG_UTF8 = 0x0800
MAX_ENTRIES = 0xFFFF - 1
MAX_SIZE = 0xFFFFFFFF - 1
"""Larger archives would need ZIP64; the reader's limits keep entries far below."""


@dataclass(frozen=True)
class OutEntry:
    name: str
    method: int
    payload: bytes
    """The bytes stored for the entry (compressed if ``method`` is deflate)."""
    crc: int
    size: int
    mode: int | None = None
    """Unix mode (file type and permission bits), or None to write none."""


def copied(archive: ZipArchive, entry: ZipEntry, name: str | None = None,
           mode: int | None = None) -> OutEntry:
    """An entry copied from ``archive`` with its compressed bytes unchanged
    (under ``name`` and with ``mode`` if given).

    The entry is read (and so checked: size, CRC, limits) first, so damaged
    or hidden data is never copied unseen.
    """
    archive.read(entry)
    return OutEntry(entry.name if name is None else name, entry.method, archive.raw(entry),
                    entry.crc, entry.size, mode)


def new(name: str, data: bytes, method: int = DEFLATED, mode: int | None = None) -> OutEntry:
    """An entry with new contents, compressed with ``method``."""
    if method == STORED:
        payload = bytes(data)
    elif method == DEFLATED:
        c = zlib.compressobj(9, zlib.DEFLATED, -15)
        payload = c.compress(data) + c.flush()
    else:
        raise ValueError(f"unsupported compression method {method}")
    return OutEntry(name, method, payload, zlib.crc32(data), len(data), mode)


def write_zip(entries: Iterable[OutEntry], first: tuple[str, ...] = ()) -> bytes:
    """The archive of ``entries``; names in ``first`` (if present) come first,
    in that order. Raises ValueError for duplicate names, a "mimetype" entry
    that is compressed, or an archive too large for plain (non-ZIP64) ZIP."""
    items = list(entries)
    names = [e.name for e in items]
    if len(set(names)) != len(names):
        raise ValueError("duplicate entry names")
    if len(items) > MAX_ENTRIES:
        raise ValueError(f"too many entries ({len(items)})")
    front = [e for name in first for e in items if e.name == name]
    items = front + [e for e in items if e.name not in first]
    if items and items[0].name == "mimetype" and items[0].method != STORED:
        raise ValueError("the OpenDocument mimetype entry must be stored uncompressed")

    out = bytearray()
    directory = bytearray()
    date, time = NO_TIME
    for e in items:
        name = e.name.encode("utf-8")
        flags = 0 if e.name.isascii() else FLAG_UTF8
        offset = len(out)
        if max(offset, e.size, len(e.payload)) > MAX_SIZE:
            raise ValueError("archive too large without ZIP64")
        common = struct.pack("<HHHHIII", flags, e.method, time, date, e.crc, len(e.payload),
                             e.size)
        made_by, attributes = VERSION, 0
        if e.mode is not None:
            made_by = UNIX_VERSION
            attributes = e.mode << 16 | (DOS_DIRECTORY if e.name.endswith("/") else 0)
        out += b"PK\x03\x04" + struct.pack("<H", VERSION) + common + struct.pack(
            "<HH", len(name), 0) + name + e.payload
        directory += (b"PK\x01\x02" + struct.pack("<HH", made_by, VERSION) + common
                      + struct.pack("<HHHHHII", len(name), 0, 0, 0, 0, attributes, offset)
                      + name)
    cd_offset = len(out)
    if cd_offset + len(directory) > MAX_SIZE:
        raise ValueError("archive too large without ZIP64")
    out += directory
    out += b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, len(items), len(items), len(directory),
                                       cd_offset, 0)
    return bytes(out)
