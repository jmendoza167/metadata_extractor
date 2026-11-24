"""Safe reading of ZIP archives (the container of DOCX, XLSX, PPTX, ODT, ...).

Every archive is untrusted. The reader never extracts anything to disk and
never trusts a size the archive declares:

- The central directory is located from the end record (ZIP64 too) and
  every entry's local header is checked against it: same name, flags,
  method, CRC and sizes. Entries must not overlap.
- Limits: number of entries, uncompressed size per entry and in total,
  compression ratio, and nesting depth (archives inside archives share one
  budget). Decompression is streamed in steps and stops as soon as a limit
  would be passed, whatever the headers say; the result must then match
  the declared size and CRC-32.
- Refused (MalformedFileError): path traversal (".."), absolute paths,
  drive letters, backslashes, control characters, duplicate names (also
  ignoring case: Office part names are case-insensitive), encrypted
  entries, compression methods other than stored and deflate, archives on
  several disks, and bytes hidden after an entry's compressed data.
- Reported as hidden data (see report_sections): the archive comment, entry
  comments, data before the first entry, between entries and after the end,
  extra fields (timestamps, NTFS and Unix information), entry timestamps
  and the operating system that "created" the entries.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from metaex.core.errors import MalformedFileError
from metaex.core.inflate import Budget
from metaex.core.reader import ByteReader
from metaex.core.result import Section

MIB = 1024 * 1024

LOCAL_SIG = b"PK\x03\x04"
CENTRAL_SIG = b"PK\x01\x02"
EOCD_SIG = b"PK\x05\x06"
ZIP64_EOCD_SIG = b"PK\x06\x06"
ZIP64_LOCATOR_SIG = b"PK\x06\x07"
DESCRIPTOR_SIG = b"PK\x07\x08"

EOCD_SIZE = 22
ZIP64_LOCATOR_SIZE = 20
ZIP64_EOCD_MIN_SIZE = 56
LOCAL_HEADER_SIZE = 30
CENTRAL_HEADER_SIZE = 46
MAX_COMMENT = 0xFFFF

STORED, DEFLATED = 0, 8
FLAG_ENCRYPTED = 0x0001
FLAG_DESCRIPTOR = 0x0008
FLAG_STRONG_ENCRYPTION = 0x0040
FLAG_UTF8 = 0x0800
FLAG_MASKED_HEADERS = 0x2000  # central directory encryption

NO_TIME = (0x0021, 0x0000)
"""(DOS date, DOS time) of 1980-01-01 00:00:00: what Office writes, and what
metaex's writer writes. Treated as "no timestamp"."""

MAX_LISTED = 50
"""At most this many fields per report section; the rest are counted."""

DECOMPRESS_STEP = MIB


class ArchiveLimitError(MalformedFileError):
    """The archive would exceed a safety limit (possible zip bomb)."""


@dataclass(frozen=True)
class ZipLimits:
    max_entries: int = 20_000
    max_entry_size: int = 256 * MIB
    """Uncompressed bytes one entry may produce."""
    max_total_size: int = 1024 * MIB
    """Uncompressed bytes all entries (and nested archives) may produce together."""
    max_ratio: int = 200
    """Largest allowed uncompressed / compressed size ..."""
    ratio_threshold: int = MIB
    """... once an entry has produced more than this many bytes."""
    max_depth: int = 2
    """Archives nested inside archives: depth 0 is the file itself."""


DEFAULT_LIMITS = ZipLimits()


@dataclass(frozen=True)
class ExtraField:
    header_id: int
    data: bytes
    where: str
    """"local" or "central"."""

    @property
    def name(self) -> str:
        return EXTRA_FIELD_NAMES.get(self.header_id, f"unknown extra field 0x{self.header_id:04x}")


@dataclass
class ZipEntry:
    index: int
    """Position in the central directory."""
    name: str
    raw_name: bytes
    flags: int
    method: int
    crc: int
    compressed_size: int
    size: int
    """Uncompressed size as declared (checked when the entry is read)."""
    dos_date: int
    dos_time: int
    created_by: int
    """"Version made by": high byte operating system, low byte ZIP version."""
    version_needed: int
    internal_attr: int
    external_attr: int
    header_offset: int
    """Start of the local header in the file."""
    data_offset: int = 0
    """Start of the compressed data."""
    record_end: int = 0
    """End of the compressed data and of the data descriptor, if any."""
    extra: list[ExtraField] = field(default_factory=list)
    comment: bytes = b""

    @property
    def is_dir(self) -> bool:
        return self.name.endswith("/")

    @property
    def data_end(self) -> int:
        return self.data_offset + self.compressed_size

    @property
    def os_name(self) -> str:
        return OS_NAMES.get(self.created_by >> 8, f"system {self.created_by >> 8}")

    @property
    def modified(self) -> datetime | None:
        return dos_datetime(self.dos_date, self.dos_time)


@dataclass(frozen=True)
class HiddenRange:
    start: int
    end: int
    where: str
    """E.g. "before the first entry", "between entries 2 and 3"."""

    @property
    def size(self) -> int:
        return self.end - self.start


@dataclass
class ZipArchive:
    data: bytes
    entries: list[ZipEntry]
    comment: bytes
    hidden: list[HiddenRange]
    zip64: bool
    limits: ZipLimits
    budget: Budget
    depth: int
    _by_name: dict[str, ZipEntry] = field(default_factory=dict, repr=False)
    _folded: dict[str, ZipEntry] = field(default_factory=dict, repr=False)
    _cache: dict[int, bytes] = field(default_factory=dict, repr=False)

    def names(self) -> list[str]:
        return [e.name for e in self.entries]

    def get(self, name: str) -> ZipEntry | None:
        return self._by_name.get(name)

    def find(self, name: str) -> ZipEntry | None:
        """Look a name up ignoring case (Office part names are case-insensitive)."""
        return self._by_name.get(name) or self._folded.get(_fold(name))

    def raw(self, entry: ZipEntry) -> bytes:
        """The entry's compressed bytes, exactly as stored."""
        return self.data[entry.data_offset:entry.data_end]

    def read(self, entry: ZipEntry | str) -> bytes:
        """The entry's uncompressed contents, within the limits and budget.

        Raises MalformedFileError (ArchiveLimitError for limits) if the
        entry is damaged, its size or CRC don't match, or a limit is hit.
        """
        if isinstance(entry, str):
            found = self.get(entry)
            if found is None:
                raise MalformedFileError(f"ZIP has no entry {entry!r}")
            entry = found
        if entry.index in self._cache:
            return self._cache[entry.index]
        refusal = entry_refusal(entry)
        if refusal is not None:
            raise MalformedFileError(refusal)
        out = _decompress(self, entry)
        self._cache[entry.index] = out
        return out

    def open_nested(self, entry: ZipEntry | str) -> ZipArchive:
        """An archive stored inside this one, sharing this one's budget."""
        return open_archive(self.read(entry), self.limits, depth=self.depth + 1,
                            budget=self.budget)


# -- opening -------------------------------------------------------------------------


def open_archive(data: bytes | bytearray | memoryview, limits: ZipLimits = DEFAULT_LIMITS, *,
                 depth: int = 0, budget: Budget | None = None,
                 strict: bool = True) -> ZipArchive:
    """Read and check the archive's structure (nothing is decompressed yet).

    Raises MalformedFileError if it is damaged or unsafe; see the module
    docstring for what is refused. With ``strict`` False, encrypted entries
    and unknown compression methods are accepted (for reporting what the
    archive's headers reveal); reading such an entry still raises (see
    entry_refusal()).
    """
    if depth > limits.max_depth:
        raise ArchiveLimitError(f"ZIP archives are nested more than {limits.max_depth} deep "
                                "(possible zip bomb)")
    data = bytes(data)
    reader = ByteReader(data, "little")
    eocd = _find_eocd(data)
    r = reader.sub(eocd, EOCD_SIZE, "ZIP end of central directory")
    r.skip(4)
    disk, cd_disk = r.u16("disk number"), r.u16("central directory disk")
    disk_entries, total = r.u16("entries on this disk"), r.u16("total entries")
    cd_size, cd_offset = r.u32("central directory size"), r.u32("central directory offset")
    comment_len = r.u16("archive comment length")
    comment = data[eocd + EOCD_SIZE:eocd + EOCD_SIZE + comment_len]
    end = eocd + EOCD_SIZE + comment_len

    zip64 = False
    records_start = eocd  # where the central directory must end
    if 0xFFFF in (disk, cd_disk, disk_entries, total) or 0xFFFFFFFF in (cd_size, cd_offset):
        zip64 = True
        z64_pos, disk, cd_disk, disk_entries, total, cd_size, cd_offset = _zip64_eocd(reader, eocd)
        records_start = z64_pos
    if disk != 0 or cd_disk != 0 or disk_entries != total:
        raise MalformedFileError("ZIP archive spans several disks; not supported")
    if total > limits.max_entries:
        raise ArchiveLimitError(f"ZIP has {total} entries (limit {limits.max_entries}; "
                                "possible zip bomb)")

    cd_start = records_start - cd_size
    if cd_start < 0:
        raise MalformedFileError("ZIP central directory size is larger than the file")
    shift = cd_start - cd_offset
    """Bytes prepended to the archive without updating its offsets (0 normally)."""
    if shift < 0:
        raise MalformedFileError(f"ZIP central directory offset {cd_offset} is past where the "
                                 f"directory really starts ({cd_start})")

    entries = _read_central_directory(reader, cd_start, cd_size, total, shift)
    archive = ZipArchive(data, entries, comment, [], zip64, limits,
                         budget if budget is not None else Budget(limits.max_total_size), depth)
    for entry in entries:
        _check_name(entry)
        folded = _fold(entry.name)
        other = archive._folded.get(folded)
        if other is not None:
            raise MalformedFileError(f"ZIP has two entries named {entry.name!r}"
                                     + ("" if other.name == entry.name else
                                        f" and {other.name!r} (names differ only in case)")
                                     + "; refused")
        archive._by_name[entry.name] = entry
        archive._folded[folded] = entry
        _check_entry_flags(entry, strict)
        _read_local_header(reader, entry, cd_start)

    archive.hidden = _hidden_ranges(entries, cd_start, end, len(data))
    return archive


def _fold(name: str) -> str:
    return name.casefold()


def _find_eocd(data: bytes) -> int:
    """Offset of the end of central directory record.

    The last signature whose comment fits in the file is used, so a
    comment can't fake a second end record; bytes after the comment are
    reported as hidden data.
    """
    lowest = max(0, len(data) - (EOCD_SIZE + MAX_COMMENT) - 64 * 1024)
    pos = data.rfind(EOCD_SIG, lowest)
    while pos >= 0:
        if pos + EOCD_SIZE <= len(data):
            comment_len = int.from_bytes(data[pos + 20:pos + 22], "little")
            if pos + EOCD_SIZE + comment_len <= len(data):
                return pos
        pos = data.rfind(EOCD_SIG, lowest, pos)
    raise MalformedFileError("not a complete ZIP archive (no end of central directory record; "
                             "the file may be truncated)")


def _zip64_eocd(reader: ByteReader, eocd: int) -> tuple[int, int, int, int, int, int, int]:
    locator = eocd - ZIP64_LOCATOR_SIZE
    if locator < 0 or reader.sub(locator, 4, "ZIP64 locator").read(4) != ZIP64_LOCATOR_SIG:
        raise MalformedFileError("ZIP needs a ZIP64 end record, but the ZIP64 locator is missing")
    r = reader.sub(locator, ZIP64_LOCATOR_SIZE, "ZIP64 locator")
    r.skip(4)
    if r.u32("ZIP64 record disk") != 0:
        raise MalformedFileError("ZIP archive spans several disks; not supported")
    z64 = r.u64("ZIP64 record offset")
    if r.u32("number of disks") > 1:
        raise MalformedFileError("ZIP archive spans several disks; not supported")
    if z64 > locator - ZIP64_EOCD_MIN_SIZE:
        raise MalformedFileError("ZIP64 end record offset is outside the file")
    rec = reader.sub(z64, locator - z64, "ZIP64 end of central directory")
    if rec.read(4) != ZIP64_EOCD_SIG:
        raise MalformedFileError("ZIP64 end record has a wrong signature")
    size = rec.u64("ZIP64 record size")
    if size + 12 != locator - z64:
        raise MalformedFileError("ZIP64 end record size does not match its position")
    rec.skip(4, "versions")
    disk, cd_disk = rec.u32("disk number"), rec.u32("central directory disk")
    disk_entries, total = rec.u64("entries on this disk"), rec.u64("total entries")
    cd_size, cd_offset = rec.u64("central directory size"), rec.u64("central directory offset")
    return z64, disk, cd_disk, disk_entries, total, cd_size, cd_offset


def _read_central_directory(reader: ByteReader, cd_start: int, cd_size: int, total: int,
                            shift: int) -> list[ZipEntry]:
    cd = reader.sub(cd_start, cd_size, "ZIP central directory")
    entries: list[ZipEntry] = []
    for index in range(total):
        if cd.read(4, "central directory header") != CENTRAL_SIG:
            raise MalformedFileError(f"ZIP central directory entry {index + 1} has a wrong "
                                     "signature")
        created_by, needed = cd.u16("version made by"), cd.u16("version needed")
        flags, method = cd.u16("flags"), cd.u16("compression method")
        dos_time, dos_date = cd.u16("time"), cd.u16("date")
        crc, csize, usize = cd.u32("CRC-32"), cd.u32("compressed size"), cd.u32("size")
        name_len, extra_len, comment_len = cd.u16("name length"), cd.u16("extra length"), \
            cd.u16("comment length")
        disk = cd.u16("disk number")
        internal, external = cd.u16("internal attributes"), cd.u32("external attributes")
        offset = cd.u32("local header offset")
        raw_name = cd.read(name_len, "entry name")
        extra_raw = cd.read(extra_len, "extra field")
        comment = cd.read(comment_len, "entry comment")
        extras = parse_extra(extra_raw, "central")
        usize, csize, offset, disk = _apply_zip64(extras, usize, csize, offset, disk, raw_name)
        if disk != 0:
            raise MalformedFileError("ZIP archive spans several disks; not supported")
        entries.append(ZipEntry(index, _decode_name(raw_name, flags), raw_name, flags, method,
                                crc, csize, usize, dos_date, dos_time, created_by, needed,
                                internal, external, offset + shift, extra=extras,
                                comment=comment))
    if cd.remaining:
        raise MalformedFileError(f"ZIP central directory has {cd.remaining} bytes after its "
                                 f"{total} entries")
    return entries


def _apply_zip64(extras: list[ExtraField], usize: int, csize: int, offset: int, disk: int,
                 raw_name: bytes) -> tuple[int, int, int, int]:
    """Replace 0xFFFFFFFF values by those of the ZIP64 extra field, in its order."""
    needed = [usize == 0xFFFFFFFF, csize == 0xFFFFFFFF, offset == 0xFFFFFFFF, disk == 0xFFFF]
    if not any(needed):
        return usize, csize, offset, disk
    z64 = next((e for e in extras if e.header_id == 0x0001), None)
    if z64 is None:
        raise MalformedFileError(f"ZIP entry {raw_name!r} needs a ZIP64 extra field, which is "
                                 "missing")
    r = ByteReader(z64.data, "little")
    values = [usize, csize, offset, disk]
    for i, need in enumerate(needed):
        if need:
            values[i] = r.u32("ZIP64 disk number") if i == 3 else r.u64("ZIP64 value")
    return values[0], values[1], values[2], values[3]


def _decode_name(raw: bytes, flags: int) -> str:
    if flags & FLAG_UTF8:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raise MalformedFileError(f"ZIP entry name {raw!r} is not valid UTF-8") from None
    return raw.decode("cp437")


def _check_name(entry: ZipEntry) -> None:
    check_entry_name(entry.name)


def check_entry_name(name: str) -> None:
    """Raise MalformedFileError for a name that is unsafe to extract (see
    the module docstring)."""
    shown = repr(name)
    if not name:
        raise MalformedFileError("ZIP has an entry with an empty name")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        raise MalformedFileError(f"ZIP entry name {shown} contains control characters; refused")
    if "\\" in name:
        raise MalformedFileError(f"ZIP entry name {shown} contains a backslash; refused")
    if name.startswith("/"):
        raise MalformedFileError(f"ZIP entry name {shown} is an absolute path; refused")
    if len(name) >= 2 and name[1] == ":" and name[0].isalpha():
        raise MalformedFileError(f"ZIP entry name {shown} starts with a drive letter; refused")
    parts = name[:-1].split("/") if name.endswith("/") else name.split("/")
    if any(p == ".." for p in parts):
        raise MalformedFileError(f"ZIP entry name {shown} goes up a folder (\"..\"; path "
                                 "traversal); refused")
    if any(p in ("", ".") for p in parts):
        raise MalformedFileError(f"ZIP entry name {shown} has an empty or \".\" path part; "
                                 "refused")


def entry_refusal(entry: ZipEntry) -> str | None:
    """Why metaex can't read an entry's data (encrypted, or compressed with a
    method other than stored and deflate), else None."""
    shown = repr(entry.name)
    if entry.flags & (FLAG_ENCRYPTED | FLAG_STRONG_ENCRYPTION | FLAG_MASKED_HEADERS) \
            or entry.method == 99:
        return (f"ZIP entry {shown} is encrypted (password-protected); metaex can't read "
                "encrypted archives; refused")
    if entry.method not in (STORED, DEFLATED):
        return (f"ZIP entry {shown} uses compression method {entry.method}; only stored and "
                "deflate are supported")
    return None


def _check_entry_flags(entry: ZipEntry, strict: bool = True) -> None:
    shown = repr(entry.name)
    refusal = entry_refusal(entry)
    if refusal is not None:
        if strict:
            raise MalformedFileError(refusal)
        return
    if entry.method == STORED and entry.compressed_size != entry.size:
        raise MalformedFileError(f"ZIP entry {shown} is stored uncompressed, but its sizes "
                                 f"differ ({entry.compressed_size} and {entry.size})")
    if entry.is_dir and entry.size != 0:
        raise MalformedFileError(f"ZIP folder entry {shown} has contents")


def _read_local_header(reader: ByteReader, entry: ZipEntry, cd_start: int) -> None:
    shown = repr(entry.name)
    if entry.header_offset > cd_start - LOCAL_HEADER_SIZE:
        raise MalformedFileError(f"ZIP entry {shown}: local header offset "
                                 f"{entry.header_offset} is outside the entry data")
    r = reader.sub(entry.header_offset, cd_start - entry.header_offset, f"ZIP entry {shown}")
    if r.read(4, "local header") != LOCAL_SIG:
        raise MalformedFileError(f"ZIP entry {shown}: no local header at offset "
                                 f"{entry.header_offset}")
    r.skip(2, "version needed")
    flags, method = r.u16("flags"), r.u16("method")
    r.skip(4, "time and date")
    crc, csize, usize = r.u32("CRC-32"), r.u32("compressed size"), r.u32("size")
    name_len, extra_len = r.u16("name length"), r.u16("extra length")
    raw_name = r.read(name_len, "local entry name")
    local_extra = parse_extra(r.read(extra_len, "local extra field"), "local")
    entry.extra = local_extra + entry.extra
    entry.data_offset = entry.header_offset + r.tell()

    mismatch = None
    if raw_name != entry.raw_name:
        mismatch = f"name {raw_name!r}"
    elif flags != entry.flags:
        mismatch = f"flags 0x{flags:04x} (central directory: 0x{entry.flags:04x})"
    elif method != entry.method:
        mismatch = f"compression method {method} (central directory: {entry.method})"
    elif not entry.flags & FLAG_DESCRIPTOR:
        z64 = next((e for e in local_extra if e.header_id == 0x0001), None)
        if (usize == 0xFFFFFFFF or csize == 0xFFFFFFFF) and z64 is not None:
            zr = ByteReader(z64.data, "little")
            if usize == 0xFFFFFFFF:
                usize = zr.u64("ZIP64 size")
            if csize == 0xFFFFFFFF:
                csize = zr.u64("ZIP64 compressed size")
        if crc != entry.crc:
            mismatch = f"CRC-32 {crc:08x} (central directory: {entry.crc:08x})"
        elif (csize, usize) != (entry.compressed_size, entry.size):
            mismatch = (f"sizes {csize}/{usize} (central directory: "
                        f"{entry.compressed_size}/{entry.size})")
    if mismatch is not None:
        raise MalformedFileError(f"ZIP entry {shown}: the local header says {mismatch}; "
                                 "the headers don't match; refused")

    if entry.data_end > cd_start:
        raise MalformedFileError(f"ZIP entry {shown}: its compressed data runs past the start of "
                                 "the central directory (the file may be truncated)")
    entry.record_end = entry.data_end
    if entry.flags & FLAG_DESCRIPTOR:
        entry.record_end = _check_descriptor(reader, entry, cd_start, local_extra)


def _check_descriptor(reader: ByteReader, entry: ZipEntry, cd_start: int,
                      local_extra: list[ExtraField]) -> int:
    """Check the data descriptor after the data against the central directory;
    return where it ends."""
    shown = repr(entry.name)
    pos = entry.data_end
    r = reader.sub(pos, cd_start - pos, f"data descriptor of {shown}")
    if r.remaining >= 4 and r.peek(4) == DESCRIPTOR_SIG:
        r.skip(4)
    wide = any(e.header_id == 0x0001 for e in local_extra)
    crc = r.u32("descriptor CRC-32")
    if wide:
        csize, usize = r.u64("descriptor compressed size"), r.u64("descriptor size")
    else:
        csize, usize = r.u32("descriptor compressed size"), r.u32("descriptor size")
    if (crc, csize, usize) != (entry.crc, entry.compressed_size, entry.size):
        raise MalformedFileError(f"ZIP entry {shown}: its data descriptor doesn't match the "
                                 "central directory; refused")
    return pos + r.tell()


def _hidden_ranges(entries: list[ZipEntry], cd_start: int, end: int,
                   size: int) -> list[HiddenRange]:
    """Bytes that belong to no entry and no directory record."""
    hidden: list[HiddenRange] = []
    ordered = sorted(entries, key=lambda e: e.header_offset)
    pos = 0
    previous: ZipEntry | None = None
    for entry in ordered:
        if entry.header_offset < pos:
            raise MalformedFileError(
                f"ZIP entries {previous.name!r} and {entry.name!r} overlap (possible zip bomb); "
                "refused")
        if entry.header_offset > pos:
            where = ("before the first entry" if previous is None
                     else f"between entries {previous.name!r} and {entry.name!r}")
            hidden.append(HiddenRange(pos, entry.header_offset, where))
        pos = entry.record_end
        previous = entry
    if cd_start > pos:
        where = "before the first entry" if previous is None else \
            f"after the last entry ({previous.name!r}), before the central directory"
        hidden.append(HiddenRange(pos, cd_start, where))
    # The central directory ends where the (ZIP64) end records start, and those
    # were checked to follow each other without a gap.
    if end < size:
        hidden.append(HiddenRange(end, size, "after the end of the archive"))
    return hidden


# -- decompression -------------------------------------------------------------------


def _decompress(archive: ZipArchive, entry: ZipEntry) -> bytes:
    shown = repr(entry.name)
    limits = archive.limits
    raw = memoryview(archive.data)[entry.data_offset:entry.data_end]
    by_ratio = max(limits.ratio_threshold, limits.max_ratio * max(1, len(raw)))
    cap = min(limits.max_entry_size, archive.budget.remaining, by_ratio)
    if cap == archive.budget.remaining and cap < min(limits.max_entry_size, by_ratio):
        reason = f"more than the {limits.max_total_size // MIB} MiB limit for the whole archive"
    elif cap == limits.max_entry_size and cap < by_ratio:
        reason = f"more than the {limits.max_entry_size // MIB} MiB limit per entry"
    else:
        reason = (f"over {limits.max_ratio} times its compressed size ({len(raw)} bytes), "
                  "more than the compression ratio limit")

    def over() -> ArchiveLimitError:
        archive.budget.remaining = 0  # further reads fail fast
        return ArchiveLimitError(f"ZIP entry {shown} expands to {reason} (possible zip bomb); "
                                 "refused")

    if entry.method == STORED:
        if len(raw) > cap:
            raise over()
        out = bytes(raw)
    else:
        out = _inflate_raw(raw, cap, shown, over)
    if len(out) != entry.size:
        raise MalformedFileError(f"ZIP entry {shown} has {len(out)} bytes, but its headers say "
                                 f"{entry.size}")
    if zlib.crc32(out) != entry.crc:
        raise MalformedFileError(f"ZIP entry {shown} has a bad CRC-32 (its data is damaged)")
    archive.budget.remaining -= len(out)
    return out


def _inflate_raw(raw: memoryview, cap: int, shown: str, over) -> bytes:
    """Raw deflate, producing at most ``cap`` bytes (one more means "too much").
    The work is bounded by the input size and ``cap``."""
    d = zlib.decompressobj(-15)
    parts: list[bytes] = []
    produced = 0
    buf = bytes(raw)
    try:
        while not d.eof:
            chunk = d.decompress(buf, min(DECOMPRESS_STEP, cap - produced + 1))
            produced += len(chunk)
            if produced > cap:
                raise over()
            parts.append(chunk)
            tail = d.unconsumed_tail
            if not chunk and len(tail) == len(buf):
                break  # no progress: the input has run out
            buf = tail
    except zlib.error as exc:
        raise MalformedFileError(f"ZIP entry {shown} has invalid compressed data ({exc})") from None
    if not d.eof:
        raise MalformedFileError(f"ZIP entry {shown} has incomplete compressed data")
    extra = len(d.unused_data) + len(d.unconsumed_tail)
    if extra:
        raise MalformedFileError(f"ZIP entry {shown} hides {extra} bytes after the end of its "
                                 "compressed data; refused")
    return b"".join(parts)


# -- extra fields, times, names --------------------------------------------------------

EXTRA_FIELD_NAMES = {
    0x0001: "ZIP64 sizes",
    0x000A: "NTFS times",
    0x000D: "Unix information (PKWARE)",
    0x5455: "extended timestamps",
    0x5855: "Unix information (Info-ZIP, old)",
    0x7855: "Unix user and group (Info-ZIP, old)",
    0x7875: "Unix user and group",
    0x7075: "Unicode path",
    0x6375: "Unicode comment",
    0xCAFE: "Java JAR marker",
    0xA220: "Microsoft padding",
    0xD935: "Android alignment",
    0x9901: "AES encryption",
}

OS_NAMES = {0: "MS-DOS", 1: "Amiga", 2: "OpenVMS", 3: "Unix", 4: "VM/CMS", 5: "Atari ST",
            6: "OS/2", 7: "Macintosh", 8: "Z-System", 9: "CP/M", 10: "Windows NTFS",
            11: "MVS", 12: "VSE", 13: "Acorn RISC", 14: "VFAT", 15: "MVS", 16: "BeOS",
            17: "Tandem", 18: "OS/400", 19: "macOS"}


def parse_extra(raw: bytes, where: str) -> list[ExtraField]:
    """Split an extra field area into its records (header ID, size, data)."""
    r = ByteReader(raw, "little")
    out = []
    while r.remaining:
        if r.remaining < 4:
            raise MalformedFileError(f"ZIP {where} extra field has {r.remaining} stray bytes")
        header_id, size = r.u16("extra field ID"), r.u16("extra field size")
        out.append(ExtraField(header_id, r.read(size, "extra field data"), where))
    return out


def dos_datetime(date: int, time: int) -> datetime | None:
    try:
        return datetime(1980 + (date >> 9), (date >> 5) & 0xF, date & 0x1F,
                        time >> 11, (time >> 5) & 0x3F, min(59, (time & 0x1F) * 2))
    except ValueError:
        return None


_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def _filetime(value: int) -> str:
    try:
        return (_FILETIME_EPOCH + timedelta(microseconds=value // 10)).replace(
            microsecond=0).isoformat()
    except OverflowError:
        return f"invalid ({value})"


def _unix_time(value: int) -> str:
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return f"invalid ({value})"


def describe_extra(extra: ExtraField) -> str:
    """What an extra field holds, for people to read."""
    r = ByteReader(extra.data, "little")
    try:
        if extra.header_id == 0x000A:  # NTFS: reserved, then tagged attributes
            r.skip(4)
            while r.remaining >= 4:
                tag, size = r.u16(), r.u16()
                if tag == 1 and size >= 24:
                    m, a, c = r.u64(), r.u64(), r.u64()
                    return (f"modified {_filetime(m)}, accessed {_filetime(a)}, "
                            f"created {_filetime(c)}")
                r.skip(size)
        elif extra.header_id == 0x5455:
            flags = r.u8()
            names = [n for bit, n in ((1, "modified"), (2, "accessed"), (4, "created"))
                     if flags & bit]
            parts = []
            for n in names:
                if r.remaining < 4:
                    break
                parts.append(f"{n} {_unix_time(r.i32())}")
            return ", ".join(parts) or "no times"
        elif extra.header_id == 0x7875:
            r.skip(1)
            uid = int.from_bytes(r.read(r.u8()), "little")
            gid = int.from_bytes(r.read(r.u8()), "little")
            return f"user ID {uid}, group ID {gid}"
        elif extra.header_id in (0x000D, 0x5855):
            atime, mtime = r.u32(), r.u32()
            text = f"accessed {_unix_time(atime)}, modified {_unix_time(mtime)}"
            if r.remaining >= 4:
                text += f", user ID {r.u16()}, group ID {r.u16()}"
            return text
        elif extra.header_id in (0x7075, 0x6375):
            r.skip(5)
            return r.read(r.remaining).decode("utf-8", errors="replace")
    except MalformedFileError:
        return f"damaged ({len(extra.data)} bytes)"
    return f"{len(extra.data)} bytes"


# -- report -------------------------------------------------------------------------


def _limited(section: Section, total: int) -> None:
    if total > MAX_LISTED:
        section.add("more", "More", f"{total - MAX_LISTED} more not listed")


def report_sections(archive: ZipArchive) -> list[Section]:
    """What the ZIP container itself reveals (not the files inside it):

    - zip_hidden: data before, between and after the entries, entry comments
    - zip_comment: the archive comment
    - zip_extra: extra fields with timestamps, user IDs and the like
    - zip_entries: entry timestamps (other than 1980-01-01 00:00) and the
      operating system that made the entries (other than MS-DOS)
    """
    sections: list[Section] = []
    hidden = Section("zip_hidden", "Hidden data in the ZIP container")
    for n, item in enumerate(archive.hidden, 1):
        hidden.add(f"range_{n}", f"Data {item.where}", archive.data[item.start:item.end],
                   sensitive=True, note=f"{item.size} bytes at offset {item.start}")
    commented = [e for e in archive.entries if e.comment]
    for e in commented[:MAX_LISTED]:
        hidden.add(f"entry_comment.{e.name}", f"Comment on {e.name}",
                   e.comment.decode("utf-8", errors="replace"), sensitive=True)
    _limited(hidden, len(commented))
    if hidden.fields:
        sections.append(hidden)

    if archive.comment:
        comment = Section("zip_comment", "ZIP archive comment")
        comment.add("comment", "Comment", archive.comment.decode("utf-8", errors="replace"),
                    sensitive=True)
        sections.append(comment)

    extras = Section("zip_extra", "ZIP extra fields")
    listed = [(e, x) for e in archive.entries for x in e.extra if x.header_id != 0x0001]
    for e, x in listed[:MAX_LISTED]:
        extras.add(f"{e.name}.{x.where}.0x{x.header_id:04x}", f"{e.name}: {x.name} ({x.where})",
                   describe_extra(x), sensitive=x.header_id in (0x000A, 0x5455, 0x000D, 0x5855,
                                                               0x7875, 0x7855, 0x7075))
    _limited(extras, len(listed))
    if extras.fields:
        sections.append(extras)

    info = Section("zip_entries", "ZIP entry information")
    timed = [e for e in archive.entries if (e.dos_date, e.dos_time) != NO_TIME]
    if timed:
        times = sorted(t for t in (e.modified for e in timed) if t is not None)
        if times:
            span = times[0].isoformat() if times[0] == times[-1] else \
                f"{times[0].isoformat()} to {times[-1].isoformat()}"
        else:
            span = "invalid"
        info.add("entry_times", "Entry modification times",
                 f"{span} ({len(timed)} of {len(archive.entries)} entries)", sensitive=True)
    systems = sorted({e.os_name for e in archive.entries if e.created_by >> 8 != 0})
    if systems:
        info.add("created_by_os", "Created on", systems)
    if info.fields:
        sections.append(info)
    return sections
