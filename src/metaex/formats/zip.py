"""Plain ZIP archives: what the archive reveals besides the files in it.

Office documents and OpenDocument files are ZIP archives too, but they are
detected as such and handled by their own modules; this module handles
every other ZIP. The archive is read with core/ziparchive.py (never
extracted to disk); its entries become core/archive.py members:

- entry times: the DOS time of every entry and the extended timestamp
  (UT), NTFS and Unix extra fields (modified, accessed, created);
- owners: the user and group IDs of the Unix extra fields (ux, Ux, UX);
- the operating system that made each entry, Unix permissions and
  Windows attributes;
- the archive comment and entry comments;
- operating system junk (__MACOSX/, AppleDouble "._" files with their
  extended attributes, .DS_Store, Thumbs.db, desktop.ini);
- names that reveal a user folder, symbolic link targets;
- hidden data: bytes before, between and after the entries (including an
  APK signing block).
"""

from __future__ import annotations

import stat
from datetime import datetime, timedelta, timezone

from metaex.core import appledouble
from metaex.core.archive import (DEVICE, DIR, FIFO, FILE, SYMLINK, Member, contents_section,
                                 junk_reason, link_problem, member_sections)
from metaex.core.errors import MalformedFileError
from metaex.core.reader import ByteReader
from metaex.core.result import Section
from metaex.core.ziparchive import (NO_TIME, OS_NAMES, ZipArchive, ZipEntry, entry_refusal,
                                    open_archive)

UNIX_SYSTEMS = (3, 19)
"""ZIP "made by" systems whose external attributes hold a Unix mode: Unix and macOS."""
DOS_SYSTEMS = (0, 10, 14)
"""MS-DOS, Windows NTFS and VFAT: the low byte holds Windows attributes."""
MAX_SPECIAL_READ = 1024 * 1024
"""Largest AppleDouble or symbolic link entry read for the report."""
MAX_STRUCTURE = 200

APK_SIG_MAGIC = b"APK Sig Block 42"
SIGNATURE_SUFFIXES = (".SF", ".RSA", ".DSA", ".EC")


# -- extra fields ----------------------------------------------------------------------------

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


TIME_FIELDS = (0x5455, 0x000A, 0x000D, 0x5855)
ID_FIELDS = (0x7875, 0x7855, 0x000D, 0x5855)
QUIET_FIELDS = (0x0001,)
"""ZIP64 sizes: structure, not metadata."""


def _apply_extra(member: Member, entry: ZipEntry) -> None:
    """Fill ``member`` from the entry's extra fields; each kind of field is
    used once, the local one first (it has more times than the central one)."""
    seen: set[int] = set()
    for x in entry.extra:
        if x.header_id in QUIET_FIELDS:
            continue
        if x.header_id in seen:
            continue
        seen.add(x.header_id)
        r = ByteReader(x.data, "little")
        try:
            if x.header_id == 0x5455:
                flags = r.u8("UT flags")
                for bit, what in ((1, "modified"), (2, "accessed"), (4, "created")):
                    if flags & bit and r.remaining >= 4:
                        member.times.append((f"{what} (extended timestamp)",
                                             _unix_time(r.i32())))
            elif x.header_id == 0x000A:
                r.skip(4, "NTFS reserved")
                while r.remaining >= 4:
                    tag, size = r.u16("NTFS tag"), r.u16("NTFS size")
                    body = ByteReader(r.read(size, "NTFS attribute"), "little")
                    if tag == 1 and size >= 24:
                        for what in ("modified", "accessed", "created"):
                            member.times.append((f"{what} (NTFS)", _filetime(body.u64())))
            elif x.header_id in (0x000D, 0x5855):
                atime, mtime = r.u32("access time"), r.u32("modification time")
                member.times.append(("accessed (Unix extra field)", _unix_time(atime)))
                member.times.append(("modified (Unix extra field)", _unix_time(mtime)))
                if r.remaining >= 4:
                    member.uid, member.gid = r.u16("user ID"), r.u16("group ID")
            elif x.header_id == 0x7875:
                r.skip(1, "ux version")
                member.uid = int.from_bytes(r.read(r.u8("uid size"), "uid"), "little")
                member.gid = int.from_bytes(r.read(r.u8("gid size"), "gid"), "little")
            elif x.header_id == 0x7855:
                if r.remaining >= 4:
                    member.uid, member.gid = r.u16("user ID"), r.u16("group ID")
            elif x.header_id in (0x7075, 0x6375):
                r.skip(5, "Unicode field header")
                text = r.read(r.remaining).decode("utf-8", errors="replace")
                member.extra.append((x.name, text))
            else:
                member.extra.append((x.name, f"{len(x.data)} bytes"))
        except MalformedFileError:
            member.extra.append((f"{x.name} (damaged)", f"{len(x.data)} bytes"))


# -- members ---------------------------------------------------------------------------------

_KINDS = {stat.S_IFDIR: DIR, stat.S_IFLNK: SYMLINK, stat.S_IFREG: FILE, stat.S_IFCHR: DEVICE,
          stat.S_IFBLK: DEVICE, stat.S_IFIFO: FIFO, stat.S_IFSOCK: DEVICE}


def unix_mode(entry: ZipEntry) -> int | None:
    """The entry's Unix mode (type and permission bits), if it records one."""
    if entry.created_by >> 8 not in UNIX_SYSTEMS:
        return None
    mode = entry.external_attr >> 16
    return mode or None


def entry_kind(entry: ZipEntry) -> str:
    if entry.is_dir:
        return DIR
    mode = unix_mode(entry)
    if mode is not None and stat.S_IFMT(mode):
        return _KINDS.get(stat.S_IFMT(mode), FILE)
    return FILE


def _readable(archive: ZipArchive, entry: ZipEntry) -> bytes | None:
    """The entry's contents for the report, if it is small and readable."""
    if entry.size > MAX_SPECIAL_READ or entry_refusal(entry) is not None:
        return None
    try:
        return archive.read(entry)
    except MalformedFileError:
        return None


def text(raw: bytes) -> str:
    """A comment: UTF-8 if it is valid UTF-8 (ZIP comments have no encoding
    flag), else the original IBM PC code page."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp437")


def members(archive: ZipArchive) -> list[Member]:
    out = []
    for entry in archive.entries:
        kind = entry_kind(entry)
        member = Member(entry.name.rstrip("/"), kind, entry.size,
                        system=OS_NAMES.get(entry.created_by >> 8,
                                            f"system {entry.created_by >> 8}"))
        mode = unix_mode(entry)
        if mode is not None:
            member.mode = stat.S_IMODE(mode)
        elif entry.created_by >> 8 in DOS_SYSTEMS:
            flags = [n for bit, n in ((0x01, "read-only"), (0x02, "hidden"), (0x04, "system"))
                     if entry.external_attr & bit]
            if flags:
                member.extra.append(("Windows attributes", ", ".join(flags)))
        if (entry.dos_date, entry.dos_time) != NO_TIME:
            when = entry.modified
            member.times.append(("modified (local time)",
                                 when.isoformat() if when else "invalid"))
        _apply_extra(member, entry)
        member.comment = text(entry.comment) if entry.comment else ""
        if kind == SYMLINK:
            target = _readable(archive, entry)
            member.link = (target.decode("utf-8", errors="replace") if target is not None
                           else "(not readable)")
        head = b""
        base = member.name.rsplit("/", 1)[-1]
        if kind == FILE and (base.startswith("._") or member.name.startswith("__MACOSX/")):
            head = _readable(archive, entry) or b""
        member.junk = junk_reason(member.name, kind, head)
        if head and appledouble.is_appledouble(head):
            member.apple = appledouble.parse_appledouble(head)
        out.append(member)
    return out


# -- signatures --------------------------------------------------------------------------------


def signature_files(archive: ZipArchive) -> list[str]:
    """JAR signature files (META-INF/*.SF, *.RSA, *.DSA, *.EC)."""
    return [e.name for e in archive.entries
            if e.name.upper().startswith("META-INF/") and "/" not in e.name[9:]
            and e.name.upper().endswith(SIGNATURE_SUFFIXES)]


def has_apk_signing_block(archive: ZipArchive) -> bool:
    """An APK signing block: it sits right before the central directory and
    ends with "APK Sig Block 42"."""
    return any(archive.data[h.start:h.end].endswith(APK_SIG_MAGIC) for h in archive.hidden)


def signed(archive: ZipArchive) -> str | None:
    """Why the archive is signed, or None."""
    files = signature_files(archive)
    if files:
        return f"it is signed ({', '.join(files[:3])})"
    if has_apk_signing_block(archive):
        return "it holds an APK signing block"
    return None


# -- report ----------------------------------------------------------------------------------


def hidden_ranges(archive: ZipArchive) -> list[tuple[str, bytes, str]]:
    return [(h.where, archive.data[h.start:h.end], f"{h.size} bytes at offset {h.start}")
            for h in archive.hidden]


def structure_section(archive: ZipArchive) -> Section:
    section = Section("structure", "ZIP structure")
    section.add("entries", "Entries", len(archive.entries))
    section.add("zip64", "ZIP64", archive.zip64)
    for e in archive.entries[:MAX_STRUCTURE]:
        method = {0: "stored", 8: "deflate"}.get(e.method, f"method {e.method}")
        section.add(f"entry.{e.index}", e.name,
                    f"{method}, {e.compressed_size} -> {e.size} bytes at offset "
                    f"{e.header_offset}")
    if len(archive.entries) > MAX_STRUCTURE:
        section.add("more", "More", f"{len(archive.entries) - MAX_STRUCTURE} more not listed")
    return section


def report(data: bytes | bytearray | memoryview) -> dict:
    """What a plain ZIP archive reveals besides its files.

    Encrypted entries and unknown compression methods don't stop the report
    (names, times and owners are not encrypted), but they are warned about.
    Raises MalformedFileError if the archive itself is damaged or unsafe.
    """
    archive = open_archive(data, strict=False)
    found = members(archive)
    warnings: list[str] = []
    refused = [(e.name, entry_refusal(e)) for e in archive.entries if entry_refusal(e)]
    if refused:
        warnings.append(f"{len(refused)} entr{'y' if len(refused) == 1 else 'ies'} can't be "
                        f"read: {refused[0][1]}; metaex can't clean this archive")
    reason = signed(archive)
    if reason is not None:
        warnings.append(f"the archive is signed: {reason}; metaex can't clean it without "
                        "breaking the signature")
    if archive.hidden:
        total = sum(h.size for h in archive.hidden)
        warnings.append(f"CRITICAL: {total} bytes of data are hidden in the ZIP archive outside "
                        "its entries")
    for m in found:
        if m.link is not None:
            problem = link_problem(m)
            if problem is not None:
                warnings.append(f"{problem}; metaex refuses to clean this archive")
        attrs = [k for k, _ in m.apple.xattrs] if m.apple is not None else []
        if appledouble.WHERE_FROMS in attrs or appledouble.QUARANTINE in attrs:
            warnings.append(f"CRITICAL: {m.name} records where a file was downloaded from")
    sections = [contents_section(found, "ZIP")]
    sections += member_sections(found, comment=text(archive.comment),
                                hidden=hidden_ranges(archive))
    return {"sections": sections, "warnings": warnings, "complete": True,
            "structure": structure_section(archive)}

