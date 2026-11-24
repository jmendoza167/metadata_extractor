"""Safe reading of TAR archives (v7, ustar, GNU and pax), never extracting.

A TAR archive is a sequence of 512-byte headers, each followed by the
entry's data padded to a multiple of 512 bytes, and ends with two zero
blocks (then, usually, zeros up to a multiple of the 10240-byte record).
Every header has a checksum. Extensions put extra headers before an
entry: GNU "L" and "K" headers hold long names and link targets, pax "x"
headers hold "key=value" records for the next entry (long names, exact
times, owner names, extended attributes, ...) and pax "g" headers hold
records for every entry after them.

Every archive is untrusted:

- every header's checksum is checked; numbers (octal or GNU base-256) are
  checked; the archive must not end inside a header or an entry's data;
- limits: number of entries, size of each entry, of pax and long-name
  headers and of pax records;
- refused (MalformedFileError, or UnsupportedFormatError for valid but
  unsupported entries): names that go up a folder (".."), absolute names,
  drive letters, backslashes and control characters; GNU sparse files,
  multi-volume archives and other unknown entry types;
- reported as hidden data: non-zero bytes in header fields that are not
  used (after a name's end, in padding and in GNU sparse fields), in the
  padding after an entry's data, and after the end of the archive; earlier
  copies of a file that a later entry with the same name replaces.
"""

from __future__ import annotations

import base64
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone

from metaex.core.errors import MalformedFileError, UnsupportedFormatError

BLOCK = 512
RECORD = 20 * BLOCK
MIB = 1024 * 1024

MAX_ENTRIES = 20_000
MAX_ENTRY_SIZE = 256 * MIB
MAX_EXTENDED_SIZE = MIB
"""Largest pax ("x", "g") or GNU long name ("L", "K") header data."""
MAX_RECORDS = 10_000
"""pax records per header."""

REGULAR = (b"0", b"\x00", b"7")
HARDLINK, SYMLINK, CHARDEV, BLOCKDEV, DIRECTORY, FIFO = b"1", b"2", b"3", b"4", b"5", b"6"
PAX, PAX_GLOBAL, SOLARIS_PAX = b"x", b"g", b"X"
GNU_LONGNAME, GNU_LONGLINK, GNU_VOLUME = b"L", b"K", b"V"
TYPE_NAMES = {b"0": "file", b"\x00": "file", b"7": "file", HARDLINK: "hard link",
              SYMLINK: "symlink", CHARDEV: "character device", BLOCKDEV: "block device",
              DIRECTORY: "folder", FIFO: "fifo", PAX: "pax header", PAX_GLOBAL:
              "pax global header", SOLARIS_PAX: "pax header", GNU_LONGNAME: "GNU long name",
              GNU_LONGLINK: "GNU long link", GNU_VOLUME: "volume label"}
REFUSED_TYPES = {b"S": "a GNU sparse file", b"M": "a multi-volume continuation",
                 b"N": "an old GNU long name entry", b"D": "a GNU incremental (dumpdir) entry"}
DATA_TYPES = REGULAR
"""Entry types whose size field counts data that follows the header."""

USTAR_MAGIC = b"ustar\x0000"
GNU_MAGIC = b"ustar  \x00"

# (name, start, end) of the ustar header fields
FIELDS = {"name": (0, 100), "mode": (100, 108), "uid": (108, 116), "gid": (116, 124),
          "size": (124, 136), "mtime": (136, 148), "chksum": (148, 156),
          "typeflag": (156, 157), "linkname": (157, 257), "magic": (257, 263),
          "version": (263, 265), "uname": (265, 297), "gname": (297, 329),
          "devmajor": (329, 337), "devminor": (337, 345), "prefix": (345, 500),
          "pad": (500, 512)}
GNU_FIELDS = {"atime": (345, 357), "ctime": (357, 369), "offset": (369, 381),
              "longnames": (381, 385), "unused": (385, 386), "sparse": (386, 482),
              "isextended": (482, 483), "realsize": (483, 495), "pad": (495, 512)}


@dataclass
class Header:
    """One 512-byte header block, decoded."""
    offset: int
    raw: bytes
    format: str
    """"v7", "ustar" or "gnu"."""
    name: bytes
    mode: int
    uid: int
    gid: int
    size: int
    mtime: int
    typeflag: bytes
    linkname: bytes
    uname: bytes
    gname: bytes
    devmajor: int
    devminor: int
    atime: int = 0
    """GNU only."""
    ctime: int = 0
    """GNU only."""
    hidden: list[tuple[str, int, int]] = field(default_factory=list)
    """(field, start, end) of non-zero bytes in unused parts of the header
    (offsets in the file)."""

    @property
    def data_offset(self) -> int:
        return self.offset + BLOCK

    @property
    def padded_size(self) -> int:
        return -(-self.size // BLOCK) * BLOCK


@dataclass
class TarEntry:
    index: int
    header: Header
    aux: list[Header]
    """The headers that came before this entry and belong to it (pax "x",
    GNU "L"/"K"), in order."""
    name: str
    """Decoded path (pax "path", GNU long name or the header's name)."""
    raw_name: bytes
    linkname: str
    raw_linkname: bytes
    size: int
    pax: dict[str, bytes]
    """The entry's own pax records (not the global ones)."""
    globals_: dict[str, bytes]
    """The global pax records in force for this entry."""

    @property
    def typeflag(self) -> bytes:
        return self.header.typeflag

    @property
    def offset(self) -> int:
        """Where the entry starts (its first auxiliary header)."""
        return self.aux[0].offset if self.aux else self.header.offset

    @property
    def data_offset(self) -> int:
        return self.header.data_offset

    @property
    def data_end(self) -> int:
        return self.header.data_offset + self.size

    @property
    def end(self) -> int:
        return self.header.data_offset + -(-self.size // BLOCK) * BLOCK

    @property
    def is_dir(self) -> bool:
        return self.typeflag == DIRECTORY

    @property
    def is_file(self) -> bool:
        return self.typeflag in REGULAR

    @property
    def kind(self) -> str:
        return TYPE_NAMES.get(self.typeflag, "entry")


@dataclass
class GlobalHeader:
    header: Header
    records: dict[str, bytes]


@dataclass
class HiddenRange:
    start: int
    end: int
    where: str

    @property
    def size(self) -> int:
        return self.end - self.start


@dataclass
class TarArchive:
    data: bytes
    entries: list[TarEntry]
    globals_: list[GlobalHeader]
    labels: list[Header]
    """GNU volume label headers."""
    hidden: list[HiddenRange]
    end: int
    """Where the end-of-archive blocks start (len(data) if there are none)."""
    end_blocks: int
    """Number of zero blocks that end the archive (2 normally)."""
    warnings: list[str]

    def read(self, entry: TarEntry) -> bytes:
        return self.data[entry.data_offset:entry.data_end]

    def by_name(self) -> dict[str, TarEntry]:
        """The entry an extraction would end with for each name (the last one)."""
        out: dict[str, TarEntry] = {}
        for e in self.entries:
            out[normalized(e.name)] = e
        return out

    def replaced(self) -> list[TarEntry]:
        """Entries a later entry with the same name replaces."""
        final = self.by_name()
        return [e for e in self.entries if final[normalized(e.name)] is not e]


def normalized(name: str) -> str:
    """A name without "./" prefixes and trailing slashes, for comparing."""
    while name.startswith("./"):
        name = name[2:]
    return name.rstrip("/") or "."


# -- numbers and strings ---------------------------------------------------------------------


def number(raw: bytes, what: str, offset: int) -> int:
    """An octal number (NUL- or space-terminated) or a GNU base-256 number."""
    if raw and raw[0] & 0x80:
        if raw[0] == 0xFF:
            raise MalformedFileError(f"TAR header at offset {offset}: negative {what}")
        value = raw[0] & 0x7F
        for b in raw[1:]:
            value = (value << 8) | b
        return value
    text = raw.split(b"\x00", 1)[0].strip(b" ")
    if not text:
        return 0
    if any(c not in b"01234567" for c in text):
        raise MalformedFileError(f"TAR header at offset {offset}: {what} is not a number "
                                 f"({text[:20]!r})")
    return int(text, 8)


def _after_terminator(raw: bytes) -> int | None:
    """Index of the first non-zero byte after a NUL-terminated string, or None."""
    end = raw.find(b"\x00")
    if end < 0:
        return None
    rest = raw[end:]
    if rest.count(0) == len(rest):
        return None
    return end + next(i for i, b in enumerate(rest) if b)


def _numeric_rest(raw: bytes) -> int | None:
    """Index of stray bytes after an octal number's terminator, or None."""
    if not raw or raw[0] & 0x80:
        return None
    i = 0
    while i < len(raw) and raw[i] in b" 01234567":
        i += 1
    rest = raw[i:]
    if not rest or rest[:1] not in (b"\x00", b" "):
        return None
    tail = rest[1:]
    if tail.strip(b"\x00 ") == b"":
        return None
    return i + 1 + next(n for n, b in enumerate(tail) if b not in (0, 0x20))


def checksum_ok(block: bytes) -> bool:
    text = block[148:156].split(b"\x00", 1)[0].strip(b" ")
    if not text or any(c not in b"01234567" for c in text):
        return False
    stored = int(text, 8)
    header = block[:148] + b" " * 8 + block[156:]
    return stored in (sum(header), sum(b - 256 if b > 127 else b for b in header))


def decode(raw: bytes) -> str:
    """Names are bytes; UTF-8 is by far the most common encoding. Other bytes
    are kept (surrogateescape) so the name can be written back exactly."""
    return raw.decode("utf-8", errors="surrogateescape")


def encode(name: str) -> bytes:
    return name.encode("utf-8", errors="surrogateescape")


# -- headers ---------------------------------------------------------------------------------


def parse_header(block: bytes, offset: int) -> Header:
    if not checksum_ok(block):
        raise MalformedFileError(f"TAR header at offset {offset} has a bad checksum (the "
                                 "archive is damaged or not a TAR archive)")
    magic = block[257:265]
    if magic == GNU_MAGIC:
        fmt = "gnu"
    elif block[257:263] == b"ustar\x00":
        fmt = "ustar"
    else:
        fmt = "v7"

    def f(name: str) -> bytes:
        start, end = FIELDS[name]
        return block[start:end]

    def num(name: str) -> int:
        return number(f(name), name, offset)

    def text(name: str) -> bytes:
        return f(name).split(b"\x00", 1)[0]

    name = text("name")
    if fmt == "ustar" and text("prefix"):
        name = text("prefix") + b"/" + name
    h = Header(offset, block, fmt, name, num("mode"), num("uid"), num("gid"), num("size"),
               num("mtime"), f("typeflag"), text("linkname"),
               text("uname") if fmt != "v7" else b"", text("gname") if fmt != "v7" else b"",
               0, 0)
    if fmt != "v7":
        h.devmajor, h.devminor = num("devmajor"), num("devminor")
    if fmt == "gnu":
        h.atime = number(block[345:357], "access time", offset)
        h.ctime = number(block[357:369], "change time", offset)
    _find_hidden(h)
    return h


def _find_hidden(h: Header) -> None:
    """Non-zero bytes where a header has nothing to hold."""
    block = h.raw

    def flag(what: str, start: int, end: int) -> None:
        h.hidden.append((what, h.offset + start, h.offset + end))

    strings = ["name", "linkname"] + (["uname", "gname"] if h.format != "v7" else [])
    if h.format == "ustar":
        strings.append("prefix")
    for name in strings:
        start, end = FIELDS[name]
        stray = _after_terminator(block[start:end])
        if stray is not None:
            flag(f"{name} field", start + stray, end)
    numbers = ["mode", "uid", "gid", "size", "mtime"] + (
        ["devmajor", "devminor"] if h.format != "v7" else [])
    for name in numbers:
        start, end = FIELDS[name]
        stray = _numeric_rest(block[start:end])
        if stray is not None:
            flag(f"{name} field", start + stray, end)
    if h.format == "v7":
        if block[257:].strip(b"\x00"):
            flag("unused part of the header", 257, BLOCK)
    elif h.format == "ustar":
        if block[500:].strip(b"\x00"):
            flag("header padding", 500, BLOCK)
    else:  # GNU: offset, long names, sparse map and padding are unused here
        for name in ("offset", "longnames", "unused", "sparse", "isextended", "realsize",
                     "pad"):
            start, end = GNU_FIELDS[name]
            raw = block[start:end]
            if name in ("offset", "realsize"):
                if raw.strip(b"\x00 0"):
                    flag(f"GNU {name} field", start, end)
            elif raw.strip(b"\x00"):
                flag(f"GNU {name} field", start, end)


def parse_pax(data: bytes, offset: int) -> dict[str, bytes]:
    """The "length key=value\\n" records of a pax header (later keys win)."""
    out: dict[str, bytes] = {}
    pos = 0
    for _ in range(MAX_RECORDS + 1):
        if pos >= len(data):
            return out
        if data[pos:].strip(b"\x00") == b"":
            return out  # some writers pad the records with NULs
        space = data.find(b" ", pos, pos + 20)
        if space < 0 or not data[pos:space].isdigit():
            raise MalformedFileError(f"pax header at offset {offset} has a malformed record")
        length = int(data[pos:space])
        record = data[pos:pos + length]
        if length <= space - pos + 1 or len(record) != length or not record.endswith(b"\n"):
            raise MalformedFileError(f"pax header at offset {offset} has a record with a wrong "
                                     "length")
        key, eq, value = record[space - pos + 1:-1].partition(b"=")
        if not eq or not key:
            raise MalformedFileError(f"pax header at offset {offset} has a record without "
                                     "\"key=value\"")
        out[key.decode("utf-8", errors="surrogateescape")] = value
        pos += length
    raise MalformedFileError(f"pax header at offset {offset} has more than {MAX_RECORDS} records")


# -- names ------------------------------------------------------------------------------------


def check_name(name: str, what: str = "TAR entry name") -> None:
    shown = repr(name)
    if not name:
        raise MalformedFileError(f"{what} is empty")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        raise MalformedFileError(f"{what} {shown} contains control characters; refused")
    if "\\" in name:
        raise MalformedFileError(f"{what} {shown} contains a backslash; refused")
    if name.startswith("/"):
        raise MalformedFileError(f"{what} {shown} is an absolute path; refused")
    if len(name) >= 2 and name[1] == ":" and name[0].isalpha():
        raise MalformedFileError(f"{what} {shown} starts with a drive letter; refused")
    if any(p == ".." for p in name.split("/")):
        raise MalformedFileError(f"{what} {shown} goes up a folder (\"..\"; path traversal); "
                                 "refused")


# -- the archive ------------------------------------------------------------------------------


def _number_record(records: dict[str, bytes], key: str, offset: int) -> int | None:
    raw = records.get(key)
    if raw is None:
        return None
    text = raw.split(b".", 1)[0]
    if not text.lstrip(b"-").isdigit():
        raise MalformedFileError(f"pax header at offset {offset}: {key} is not a number")
    return int(text)


def open_tar(data: bytes | bytearray | memoryview, *, max_entries: int = MAX_ENTRIES,
             max_entry_size: int = MAX_ENTRY_SIZE) -> TarArchive:
    """Read every header of a TAR archive (the data is not copied).

    Raises MalformedFileError if the archive is damaged or unsafe and
    UnsupportedFormatError for entries metaex doesn't handle (GNU sparse
    files, multi-volume archives, unknown types).
    """
    data = bytes(data)
    entries: list[TarEntry] = []
    globals_: list[GlobalHeader] = []
    labels: list[Header] = []
    hidden: list[HiddenRange] = []
    warnings: list[str] = []
    global_records: dict[str, bytes] = {}
    aux: list[Header] = []
    pax: dict[str, bytes] = {}
    long_name: bytes | None = None
    long_link: bytes | None = None
    pos = 0
    end = len(data)
    end_blocks = 0
    headers = 0
    while True:
        if pos == len(data):
            if aux:
                raise MalformedFileError("TAR archive ends after an extended header, before "
                                         "its entry (the file may be truncated)")
            warnings.append("the TAR archive has no end-of-archive blocks")
            break
        if pos + BLOCK > len(data):
            raise MalformedFileError(f"TAR archive ends inside a header at offset {pos} (the "
                                     "file may be truncated)")
        block = data[pos:pos + BLOCK]
        if block.count(0) == BLOCK:
            if aux:
                raise MalformedFileError(f"TAR archive ends at offset {pos} after an extended "
                                         "header, before its entry")
            end = pos
            end_blocks = 1
            if data[pos + BLOCK:pos + 2 * BLOCK] == bytes(BLOCK):
                end_blocks = 2
            else:
                warnings.append("the TAR archive ends with one zero block instead of two")
            after = pos + end_blocks * BLOCK
            rest = data[after:]
            if rest.strip(b"\x00"):
                first = after + next(i for i, b in enumerate(rest) if b)
                hidden.append(HiddenRange(first, len(data), "after the end of the archive"))
            break
        headers += 1
        if headers > 4 * max_entries:
            raise MalformedFileError(f"TAR archive has more than {max_entries} entries; refused")
        h = parse_header(block, pos)
        for what, start, stop in h.hidden:
            hidden.append(HiddenRange(start, stop, f"in the {what} of the header at offset "
                                                   f"{pos}"))
        flag = h.typeflag
        size = h.size
        if flag in REFUSED_TYPES:
            raise UnsupportedFormatError(f"the TAR archive contains {REFUSED_TYPES[flag]} "
                                         f"(at offset {pos}), which metaex doesn't support")
        if flag in (PAX, SOLARIS_PAX, PAX_GLOBAL, GNU_LONGNAME, GNU_LONGLINK, GNU_VOLUME):
            if size > MAX_EXTENDED_SIZE:
                raise MalformedFileError(f"TAR extended header at offset {pos} is {size} bytes "
                                         f"(limit {MAX_EXTENDED_SIZE}); refused")
        elif flag not in TYPE_NAMES:
            raise UnsupportedFormatError(f"the TAR archive contains an entry of unknown type "
                                         f"{flag!r} at offset {pos}; refused")
        records: dict[str, bytes] = {}
        if flag in (PAX, SOLARIS_PAX):
            records = parse_pax(_data(data, h, size), pos)
            if any(k.startswith("GNU.sparse") for k in records):
                raise UnsupportedFormatError(f"the TAR archive contains a GNU sparse file (at "
                                             f"offset {pos}), which metaex doesn't support")
            pax.update(records)
            aux.append(h)
        elif flag == PAX_GLOBAL:
            if aux:
                raise MalformedFileError(f"TAR global header at offset {pos} comes between an "
                                         "extended header and its entry")
            records = parse_pax(_data(data, h, size), pos)
            globals_.append(GlobalHeader(h, records))
            global_records.update(records)
        elif flag in (GNU_LONGNAME, GNU_LONGLINK):
            value = _data(data, h, size).split(b"\x00", 1)[0]
            if flag == GNU_LONGNAME:
                long_name = value
            else:
                long_link = value
            aux.append(h)
        elif flag == GNU_VOLUME:
            labels.append(h)
            _data(data, h, size)
        else:
            if "size" in pax or "size" in global_records:
                size = _number_record({**global_records, **pax}, "size", pos)
            if flag not in DATA_TYPES and size:
                raise MalformedFileError(f"TAR {TYPE_NAMES[flag]} at offset {pos} has a size "
                                         f"({size}); refused")
            if size > max_entry_size:
                raise MalformedFileError(f"TAR entry at offset {pos} is {size} bytes (limit "
                                         f"{max_entry_size // MIB} MiB); refused")
            raw_name = h.name
            raw_link = h.linkname
            if long_name is not None:
                raw_name = long_name
            if long_link is not None:
                raw_link = long_link
            merged = {**global_records, **pax}
            name = decode(raw_name)
            link = decode(raw_link)
            if "path" in merged:
                name = merged["path"].decode("utf-8", errors="surrogateescape")
                raw_name = encode(name)
            if "linkpath" in merged:
                link = merged["linkpath"].decode("utf-8", errors="surrogateescape")
                raw_link = encode(link)
            check_name(name)
            if len(entries) >= max_entries:
                raise MalformedFileError(f"TAR archive has more than {max_entries} entries; "
                                         "refused")
            entry = TarEntry(len(entries), h, aux, name, raw_name, link, raw_link, size,
                             dict(pax), dict(global_records))
            if entry.data_offset + entry.end - entry.data_offset > len(data):
                raise MalformedFileError(f"TAR entry {name!r} at offset {pos} runs past the end "
                                         "of the file (the file may be truncated)")
            padding = data[entry.data_end:entry.end]
            if padding.strip(b"\x00"):
                hidden.append(HiddenRange(entry.data_end, entry.end,
                                          f"in the padding after {name!r}"))
            entries.append(entry)
            aux, pax, long_name, long_link = [], {}, None, None
            pos = entry.end
            continue
        pos = h.data_offset + h.padded_size
    return TarArchive(data, entries, globals_, labels, hidden, end, end_blocks, warnings)


def _data(data: bytes, h: Header, size: int) -> bytes:
    if h.data_offset + h.padded_size > len(data):
        raise MalformedFileError(f"TAR header at offset {h.offset} says {size} bytes follow, "
                                 "more than the file holds (the file may be truncated)")
    return data[h.data_offset:h.data_offset + size]


# -- values for people ------------------------------------------------------------------------


def unix_time(value: int | float) -> str:
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return f"invalid ({value})"


def pax_time(raw: bytes) -> str:
    text = raw.decode("ascii", errors="replace")
    try:
        return unix_time(float(text))
    except ValueError:
        return f"invalid ({text})"


XATTR_PREFIXES = ("SCHILY.xattr.", "LIBARCHIVE.xattr.")


def xattr(key: str, value: bytes) -> tuple[str, bytes] | None:
    """(attribute name, value) of a pax extended attribute record, else None.
    libarchive URL-encodes the name and base64-encodes the value."""
    if key.startswith("SCHILY.xattr."):
        return key[len("SCHILY.xattr."):], value
    if key.startswith("LIBARCHIVE.xattr."):
        name = urllib.parse.unquote(key[len("LIBARCHIVE.xattr."):])
        try:
            padded = value + b"=" * (-len(value) % 4)
            return name, base64.b64decode(padded, validate=True)
        except (ValueError, base64.binascii.Error):
            return name, value
    return None
