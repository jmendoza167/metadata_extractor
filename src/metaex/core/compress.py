"""Safe reading of gzip, bzip2 and xz files: the wrapper and what it holds.

These formats compress one stream of bytes: a single file (photo.jpg.gz)
or a TAR archive (backup.tar.gz). Only gzip has metadata of its own; every
gzip member header can hold the original file name (FNAME), its
modification time (MTIME), the operating system (OS), a comment
(FCOMMENT), extra fields (FEXTRA) and a header CRC (FHCRC). bzip2 and xz
headers hold only technical values (block size, integrity check).

Every file is untrusted:

- decompression is streamed in steps with a hard output limit, and stops
  as soon as a limit would be passed: the size of what one file may
  expand to, the compression ratio (after the first MiB), and the budget
  shared with everything else read from the same file;
- each member's or stream's integrity check is verified (gzip CRC-32 and
  size; bzip2 and xz by the decompressor, xz stream footers here too);
- several members or streams one after the other are read in order;
- zero bytes after the last one are padding; anything else after it is
  hidden data (gzip, bzip2: reported, and removed by strip), or makes an
  xz file malformed (xz allows only padding in multiples of four zero
  bytes).
"""

from __future__ import annotations

import bz2
import lzma
import struct
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from metaex.core.errors import MalformedFileError
from metaex.core.inflate import Budget
from metaex.core.result import Section
from metaex.core.ziparchive import DEFAULT_LIMITS, MIB, ArchiveLimitError, ZipLimits

GZIP, BZIP2, XZ = "gzip", "bzip2", "xz"
NAMES = {GZIP: "gzip", BZIP2: "bzip2", XZ: "xz"}

GZIP_MAGIC = b"\x1f\x8b\x08"
BZIP2_MAGIC = b"BZh"
XZ_MAGIC = b"\xfd7zXZ\x00"
XZ_FOOTER_MAGIC = b"YZ"

FTEXT, FHCRC, FEXTRA, FNAME, FCOMMENT = 0x01, 0x02, 0x04, 0x08, 0x10
FRESERVED = 0xE0
MAX_HEADER_STRING = 64 * 1024
"""Longest gzip file name or comment read (they end with a zero byte)."""
MAX_MEMBERS = 10_000
STEP = MIB
"""Input fed, and output asked for, at a time."""

GZIP_OS = {0: "FAT (MS-DOS, Windows)", 1: "Amiga", 2: "VMS", 3: "Unix", 4: "VM/CMS",
           5: "Atari TOS", 6: "HPFS (OS/2)", 7: "Macintosh", 8: "Z-System", 9: "CP/M",
           10: "TOPS-20", 11: "NTFS (Windows)", 12: "QDOS", 13: "Acorn RISCOS", 255: "unknown"}
NEUTRAL_OS = 255
XZ_CHECKS = {0: "none", 1: "CRC32", 4: "CRC64", 10: "SHA-256"}


@dataclass
class Member:
    """One gzip member, bzip2 stream or xz stream."""
    start: int
    end: int
    """Where it ends (after the gzip trailer, the bzip2 end marker, the xz footer)."""
    data_start: int = 0
    """Where the compressed data starts (after the gzip header)."""
    data_end: int = 0
    """Where the compressed data ends (before the gzip trailer)."""
    size: int = 0
    """Uncompressed bytes."""
    flags: int = 0
    mtime: int = 0
    xfl: int = 0
    os: int = NEUTRAL_OS
    name: bytes | None = None
    comment: bytes | None = None
    extra: bytes | None = None
    header_crc: int | None = None
    level: int = 9
    """bzip2: the block size digit (1-9)."""
    check: int = 0
    """xz: the integrity check type."""


@dataclass
class Compressed:
    kind: str
    data: bytes
    members: list[Member]
    content: bytes
    """Everything decompressed, members one after the other."""
    padding: int = 0
    """Zero bytes after the last member."""
    trailing: tuple[int, int] | None = None
    """(start, end) of non-zero data after the last member."""
    warnings: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return NAMES[self.kind]

    @property
    def end(self) -> int:
        return self.members[-1].end if self.members else 0


def kind_of(data: bytes) -> str | None:
    if data.startswith(GZIP_MAGIC):
        return GZIP
    if data.startswith(XZ_MAGIC):
        return XZ
    if _is_bzip2(data, 0):
        return BZIP2
    return None


def _is_bzip2(data: bytes, pos: int) -> bool:
    head = data[pos:pos + 10]
    return (len(head) == 10 and head[:3] == BZIP2_MAGIC and 0x31 <= head[3] <= 0x39
            and head[4:10] in (b"1AY&SY", b"\x17rE8P\x90"))


# -- limits ------------------------------------------------------------------------------------


class _Output:
    """Collects decompressed output within the limits."""

    def __init__(self, kind: str, compressed_size: int, limits: ZipLimits, budget: Budget):
        self.parts: list[bytes] = []
        self.size = 0
        self.kind = kind
        self.limits = limits
        self.budget = budget
        by_ratio = max(limits.ratio_threshold, limits.max_ratio * max(1, compressed_size))
        self.cap = min(limits.max_total_size, budget.remaining, by_ratio)
        if self.cap == by_ratio and by_ratio < min(limits.max_total_size, budget.remaining):
            self.reason = (f"over {limits.max_ratio} times its compressed size "
                           f"({compressed_size} bytes), more than the compression ratio limit")
        elif self.cap == budget.remaining and budget.remaining < limits.max_total_size:
            self.reason = "more than what is left of the size limit for this file"
        else:
            self.reason = f"more than the {limits.max_total_size // MIB} MiB limit"

    @property
    def room(self) -> int:
        """Output to ask for next: one byte more than allowed means "too much"."""
        return max(1, min(STEP, self.cap - self.size + 1))

    def add(self, piece: bytes) -> None:
        self.size += len(piece)
        if self.size > self.cap:
            self.budget.remaining = 0  # further reads fail fast
            raise ArchiveLimitError(f"the {NAMES[self.kind]} data expands to {self.reason} "
                                    "(possible decompression bomb); refused")
        if piece:
            self.parts.append(piece)

    def take(self) -> bytes:
        self.budget.remaining -= self.size
        return b"".join(self.parts)


# -- gzip --------------------------------------------------------------------------------------


def _cstring(data: bytes, pos: int, what: str) -> tuple[bytes, int]:
    end = data.find(b"\x00", pos, pos + MAX_HEADER_STRING + 1)
    if end < 0:
        raise MalformedFileError(f"gzip {what} at offset {pos} has no end (or is longer than "
                                 f"{MAX_HEADER_STRING} bytes; the file may be truncated)")
    return data[pos:end], end + 1


def _gzip_header(data: bytes, pos: int) -> Member:
    if len(data) < pos + 10:
        raise MalformedFileError(f"gzip header at offset {pos} is truncated")
    if data[pos:pos + 3] != GZIP_MAGIC:
        raise MalformedFileError(f"no gzip header at offset {pos}")
    flags, mtime, xfl, os_ = struct.unpack_from("<BIBB", data, pos + 3)
    if flags & FRESERVED:
        raise MalformedFileError(f"gzip header at offset {pos} has reserved flags set "
                                 f"(0x{flags:02x})")
    m = Member(pos, 0, flags=flags, mtime=mtime, xfl=xfl, os=os_)
    p = pos + 10
    if flags & FEXTRA:
        if len(data) < p + 2:
            raise MalformedFileError(f"gzip extra field at offset {p} is truncated")
        xlen = struct.unpack_from("<H", data, p)[0]
        if len(data) < p + 2 + xlen:
            raise MalformedFileError(f"gzip extra field at offset {p} is truncated")
        m.extra = data[p + 2:p + 2 + xlen]
        p += 2 + xlen
    if flags & FNAME:
        m.name, p = _cstring(data, p, "file name")
    if flags & FCOMMENT:
        m.comment, p = _cstring(data, p, "comment")
    if flags & FHCRC:
        if len(data) < p + 2:
            raise MalformedFileError(f"gzip header CRC at offset {p} is truncated")
        m.header_crc = struct.unpack_from("<H", data, p)[0]
        if m.header_crc != zlib.crc32(data[pos:p]) & 0xFFFF:
            raise MalformedFileError(f"gzip header at offset {pos} has a bad header CRC")
        p += 2
    m.data_start = p
    return m


def _gzip_member(data: bytes, pos: int, out: _Output) -> Member:
    m = _gzip_header(data, pos)
    d = zlib.decompressobj(-15)
    feed = m.data_start
    pending = b""
    start_size = out.size
    crc = 0
    try:
        while not d.eof:
            if not pending:
                pending = data[feed:feed + STEP]
                feed += len(pending)
                if not pending:
                    break
            piece = d.decompress(pending, out.room)
            out.add(piece)
            crc = zlib.crc32(piece, crc)
            pending = d.unconsumed_tail
        while not d.eof:  # all input given; the stream is not finished
            piece = d.decompress(b"", out.room)
            if not piece:
                break
            out.add(piece)
            crc = zlib.crc32(piece, crc)
    except zlib.error as exc:
        raise MalformedFileError(f"gzip member at offset {pos} has invalid compressed data "
                                 f"({exc})") from None
    if not d.eof:
        raise MalformedFileError(f"gzip member at offset {pos} is truncated (its compressed "
                                 "data doesn't end)")
    m.data_end = feed - len(d.unused_data) - len(pending)
    if len(data) < m.data_end + 8:
        raise MalformedFileError(f"gzip member at offset {pos} is truncated (no trailer)")
    want_crc, isize = struct.unpack_from("<II", data, m.data_end)
    m.size = out.size - start_size
    if want_crc != crc:
        raise MalformedFileError(f"gzip member at offset {pos} has a bad CRC-32 (its data is "
                                 "damaged)")
    if isize != m.size & 0xFFFFFFFF:
        raise MalformedFileError(f"gzip member at offset {pos} has a wrong size in its trailer")
    m.end = m.data_end + 8
    return m


# -- bzip2 and xz ------------------------------------------------------------------------------


def _stream(data: bytes, pos: int, decompressor, out: _Output, what: str) -> int:
    """Decompress one stream starting at ``pos``; return where it ends."""
    feed = pos
    try:
        while not decompressor.eof:
            if decompressor.needs_input:
                chunk = data[feed:feed + STEP]
                feed += len(chunk)
                if not chunk:
                    break
            else:
                chunk = b""
            out.add(decompressor.decompress(chunk, out.room))
    except (OSError, EOFError, lzma.LZMAError, ValueError) as exc:
        raise MalformedFileError(f"{what} at offset {pos} has invalid compressed data "
                                 f"({exc})") from None
    if not decompressor.eof:
        raise MalformedFileError(f"{what} at offset {pos} is truncated (its compressed data "
                                 "doesn't end)")
    return feed - len(decompressor.unused_data)


def _bzip2_stream(data: bytes, pos: int, out: _Output) -> Member:
    start_size = out.size
    end = _stream(data, pos, bz2.BZ2Decompressor(), out, "bzip2 stream")
    return Member(pos, end, pos, end, out.size - start_size, level=data[pos + 3] - 0x30)


def _xz_stream(data: bytes, pos: int, out: _Output) -> Member:
    head = data[pos:pos + 12]
    if len(head) < 12 or head[:6] != XZ_MAGIC:
        raise MalformedFileError(f"xz stream header at offset {pos} is missing or truncated")
    flags = head[6:8]
    if struct.unpack_from("<I", head, 8)[0] != zlib.crc32(flags):
        raise MalformedFileError(f"xz stream header at offset {pos} has a bad CRC-32")
    start_size = out.size
    decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
    end = _stream(data, pos, decompressor, out, "xz stream")
    footer = data[end - 12:end]
    if (len(footer) != 12 or footer[10:12] != XZ_FOOTER_MAGIC or footer[8:10] != flags
            or struct.unpack_from("<I", footer, 0)[0] != zlib.crc32(footer[4:10])):
        raise MalformedFileError(f"xz stream at offset {pos} has an invalid footer")
    return Member(pos, end, pos + 12, end - 12, out.size - start_size, check=flags[1] & 0x0F)


# -- the file ------------------------------------------------------------------------------------


def open_compressed(data: bytes | bytearray | memoryview, limits: ZipLimits = DEFAULT_LIMITS,
                    budget: Budget | None = None) -> Compressed:
    """Read and decompress a gzip, bzip2 or xz file within the limits.

    Raises MalformedFileError if it is damaged, truncated or would expand
    past a limit (ArchiveLimitError).
    """
    data = bytes(data)
    kind = kind_of(data)
    if kind is None:
        raise MalformedFileError("not a gzip, bzip2 or xz file")
    if budget is None:
        budget = Budget(limits.max_total_size)
    out = _Output(kind, len(data), limits, budget)
    members: list[Member] = []
    pos = 0
    result = Compressed(kind, data, members, b"")
    while True:
        if len(members) >= MAX_MEMBERS:
            raise MalformedFileError(f"the {NAMES[kind]} file has more than {MAX_MEMBERS} "
                                     "members")
        if kind == GZIP:
            members.append(_gzip_member(data, pos, out))
        elif kind == BZIP2:
            members.append(_bzip2_stream(data, pos, out))
        else:
            members.append(_xz_stream(data, pos, out))
        pos = members[-1].end
        if kind == XZ:
            padded = pos
            while padded < len(data) and data[padded] == 0:
                padded += 1
            if padded == len(data):
                if (padded - pos) % 4:
                    raise MalformedFileError("the xz file ends with stream padding that is not "
                                             "a multiple of four bytes")
                result.padding = padded - pos
                break
            if data[padded:padded + 6] != XZ_MAGIC or (padded - pos) % 4:
                raise MalformedFileError(f"the xz file has data after its stream (at offset "
                                         f"{padded}) that is not another stream; refused")
            pos = padded
            continue
        if pos == len(data):
            break
        if (kind == GZIP and data.startswith(GZIP_MAGIC, pos)) or (
                kind == BZIP2 and _is_bzip2(data, pos)):
            continue
        rest = data[pos:]
        if rest.count(0) == len(rest):
            result.padding = len(rest)
        else:
            result.trailing = (pos, len(data))
        break
    result.content = out.take()
    return result


# -- for people --------------------------------------------------------------------------------


def unix_time(value: int) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def describe_extra(raw: bytes) -> str:
    """The subfields of a gzip extra field: two-letter IDs and their sizes."""
    parts = []
    pos = 0
    while pos + 4 <= len(raw):
        sub_id = raw[pos:pos + 2]
        size = struct.unpack_from("<H", raw, pos + 2)[0]
        if pos + 4 + size > len(raw):
            break
        what = "BGZF block size" if sub_id == b"BC" else f"{size} bytes"
        parts.append(f"{sub_id.decode('latin-1')!r}: {what}")
        pos += 4 + size
    if pos != len(raw):
        parts.append(f"{len(raw) - pos} bytes not in subfields")
    return "; ".join(parts) or f"{len(raw)} bytes"


# -- report ------------------------------------------------------------------------------------


def text(raw: bytes) -> str:
    """gzip names and comments are ISO 8859-1 by the standard; most tools
    write UTF-8."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def inner_type(c: Compressed):
    from metaex.core import filetype

    return filetype.detect(c.content)


def contents_section(c: Compressed) -> Section:
    from metaex.core import filetype

    s = Section("compress_contents", f"{c.name} contents")
    s.add("format", "Format", c.name)
    s.add("members", "Members" if c.kind == GZIP else "Streams", len(c.members))
    s.add("compressed_size", "Compressed size (bytes)", len(c.data))
    s.add("size", "Uncompressed size (bytes)", len(c.content))
    detected = inner_type(c)
    s.add("inner", "Contents", detected.name if detected.key != filetype.UNKNOWN.key
          else "a file of unrecognized type")
    if c.kind == BZIP2:
        s.add("block_size", "Block size", f"{c.members[0].level}00 kB")
    if c.kind == XZ:
        s.add("check", "Integrity check", XZ_CHECKS.get(c.members[0].check,
                                                       f"type {c.members[0].check}"))
    if c.padding:
        s.add("padding", "Zero padding after the end (bytes)", c.padding)
    return s


def header_section(c: Compressed) -> Section:
    s = Section("compress_header", "gzip header")
    several = len(c.members) > 1
    for n, m in enumerate(c.members, 1):
        key = f".{n}" if several else ""
        where = f" (member {n})" if several else ""
        if m.name is not None:
            s.add(f"name{key}", f"Original file name{where}", text(m.name), sensitive=True)
        if m.mtime:
            s.add(f"mtime{key}", f"Modification time{where}", unix_time(m.mtime), sensitive=True)
        if m.os != NEUTRAL_OS:
            s.add(f"os{key}", f"Operating system{where}", GZIP_OS.get(m.os, f"system {m.os}"))
        if m.comment is not None:
            s.add(f"comment{key}", f"Comment{where}", text(m.comment), sensitive=True)
        if m.extra is not None:
            s.add(f"extra{key}", f"Extra field{where}", describe_extra(m.extra))
        if m.flags & FTEXT:
            s.add(f"text{key}", f"Text flag{where}", "set")
    return s


def hidden_section(c: Compressed) -> Section:
    s = Section("compress_hidden", f"Hidden data in the {c.name} file")
    if c.trailing is not None:
        start, end = c.trailing
        s.add("trailing", "Data after the end", c.data[start:end], sensitive=True,
              note=f"{end - start} bytes at offset {start}")
    return s


def report(c: Compressed) -> dict:
    """The wrapper's sections (contents, header, hidden data) and warnings."""
    warnings = list(c.warnings)
    if c.trailing is not None:
        start, end = c.trailing
        warnings.append(f"CRITICAL: {end - start} bytes of data are hidden after the end of the "
                        f"{c.name} data")
    sections = [contents_section(c)] + [s for s in (header_section(c), hidden_section(c))
                                        if s.fields]
    return {"sections": sections, "warnings": warnings, "complete": True}


# -- the formats: parse, strip, verify --------------------------------------------------------


def _tar_module():
    """formats/tar.py, found through the registry (like core/embedded.py does
    for images), for a TAR inside a compressed file."""
    from metaex.core import filetype
    from metaex.core.registry import format_module

    return format_module(filetype.TAR)


def is_tar(c: Compressed) -> bool:
    from metaex.core import filetype

    return inner_type(c).key == filetype.TAR.key


def parse(data: bytes | bytearray | memoryview, kind: str) -> dict:
    """The wrapper's metadata and, for a compressed TAR, the TAR's."""
    c = open_compressed(data)
    if c.kind != kind:
        raise MalformedFileError(f"not a {NAMES[kind]} file")
    out = report(c)
    if is_tar(c):
        from metaex.core import tar as tarcore

        inner = _tar_module().report_archive(tarcore.open_tar(c.content))
        out["sections"] += inner["sections"]
        out["warnings"] += inner["warnings"]
        out["structure"] = inner.get("structure")
    return out


# -- writing -----------------------------------------------------------------------------------

NEUTRAL_GZIP_HEADER = GZIP_MAGIC + b"\x00" + bytes(4) + b"\x00" + bytes([NEUTRAL_OS])
"""No flags, MTIME 0, XFL 0, OS 255 (unknown)."""


def rewrap(c: Compressed, content: bytes | None) -> bytes:
    """The cleaned wrapper around ``content``, or around the original
    content if ``content`` is None.

    Unchanged content keeps its compressed bytes: each gzip member's
    deflate data and trailer behind a neutral header, the bzip2 and xz
    streams as they are, without padding or data after them. New content
    is compressed again (gzip: deflate level 9 in one member; bzip2: the
    original's block size; xz: the original's integrity check).
    """
    if content is None:
        if c.kind == GZIP:
            return b"".join(NEUTRAL_GZIP_HEADER + c.data[m.data_start:m.end] for m in c.members)
        return b"".join(c.data[m.start:m.end] for m in c.members)
    if c.kind == GZIP:
        z = zlib.compressobj(9, zlib.DEFLATED, -15)
        body = z.compress(content) + z.flush()
        return (NEUTRAL_GZIP_HEADER + body
                + struct.pack("<II", zlib.crc32(content), len(content) & 0xFFFFFFFF))
    if c.kind == BZIP2:
        return bz2.compress(content, c.members[0].level if c.members else 9)
    check = c.members[0].check if c.members else lzma.CHECK_CRC64
    if check not in (lzma.CHECK_NONE, lzma.CHECK_CRC32, lzma.CHECK_CRC64, lzma.CHECK_SHA256):
        check = lzma.CHECK_CRC64
    return lzma.compress(content, format=lzma.FORMAT_XZ, check=check)


def strip(data: bytes | bytearray | memoryview, kind: str) -> bytes:
    """A cleaned copy: the wrapper rewritten without metadata (see rewrap())
    around a cleaned TAR, or around the original single file."""
    c = open_compressed(data)
    if c.kind != kind:
        raise MalformedFileError(f"not a {NAMES[kind]} file")
    content = None
    if is_tar(c):
        from metaex.core import tar as tarcore

        cleaned_tar = _tar_module().strip_archive(tarcore.open_tar(c.content))
        if cleaned_tar != c.content:
            content = cleaned_tar
    cleaned = rewrap(c, content)
    check = open_compressed(cleaned)
    if check.content != (c.content if content is None else content):
        raise MalformedFileError("internal check failed: the contents changed while stripping")
    return cleaned


def removed_sizes(data: bytes | bytearray | memoryview) -> dict[str, int]:
    try:
        c = open_compressed(data)
    except MalformedFileError:
        return {}
    out = {}
    if c.trailing is not None:
        out["compress_hidden"] = c.trailing[1] - c.trailing[0]
    if is_tar(c):
        out.update(_tar_module().removed_sizes(c.content))
    return out


def strip_warnings(data: bytes) -> list[str]:
    try:
        c = open_compressed(data)
    except MalformedFileError:
        return []
    return _tar_module().strip_warnings(c.content) if is_tar(c) else []


def kept_warnings(cleaned: bytes) -> list[str]:
    try:
        c = open_compressed(cleaned)
    except MalformedFileError:
        return []
    return _tar_module().kept_warnings(c.content) if is_tar(c) else []
