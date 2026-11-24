"""TAR archives (v7, ustar, GNU, pax): what the archive reveals besides the
files in it.

The archive is read with core/tar.py (never extracted to disk); its
entries become core/archive.py members:

- entry times: the modification time of every header, the access and
  change times of GNU headers and pax records (mtime, atime, ctime,
  LIBARCHIVE.creationtime);
- owners: user and group names and numbers (header fields or pax uname,
  gname, uid, gid);
- permissions other than the neutral 644/755;
- extended attributes (pax SCHILY.xattr.* and LIBARCHIVE.xattr.*), pax
  comments, global pax headers, GNU volume labels;
- operating system junk (AppleDouble "._" files, .DS_Store, ...);
- names that reveal a user folder, link targets;
- hidden data: non-zero bytes in unused header fields, in the padding
  after an entry's data and after the end of the archive, and earlier
  copies of files replaced by a later entry with the same name.
"""

from __future__ import annotations

import hashlib

from metaex.core import appledouble, tarwriter
from metaex.core import tar as tarcore
from metaex.core.archive import (DEVICE, DIR, FIFO, FILE, HARDLINK, NEUTRAL_TIME, SYMLINK,
                                 Member, contents_section, junk_reason, junk_warnings,
                                 link_problem, member_sections, neutral_mode, some,
                                 user_folder_warnings)
from metaex.core.errors import CannotCleanError, MalformedFileError, MetaexError
from metaex.core.result import Section

MAX_SPECIAL_READ = 1024 * 1024
MAX_STRUCTURE = 200

_KINDS = {tarcore.HARDLINK: HARDLINK, tarcore.SYMLINK: SYMLINK, tarcore.CHARDEV: DEVICE,
          tarcore.BLOCKDEV: DEVICE, tarcore.DIRECTORY: DIR, tarcore.FIFO: FIFO}

TIME_KEYS = (("mtime", "modified"), ("atime", "accessed"), ("ctime", "changed"),
             ("LIBARCHIVE.creationtime", "created"))
KNOWN_KEYS = {"path", "linkpath", "size", "uid", "gid", "uname", "gname", "mtime", "atime",
              "ctime", "LIBARCHIVE.creationtime", "comment", "charset", "hdrcharset"}


def kind_of(entry: tarcore.TarEntry) -> str:
    return _KINDS.get(entry.typeflag, FILE)


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _records_into(member: Member, records: dict[str, bytes], where: str) -> None:
    """pax records (other than names and sizes) into ``member``."""
    for key, what in TIME_KEYS:
        if key in records:
            member.times.append((f"{what} (pax{where})", tarcore.pax_time(records[key])))
    if "uname" in records:
        member.uname = _text(records["uname"])
    if "gname" in records:
        member.gname = _text(records["gname"])
    for key in ("uid", "gid"):
        if key in records:
            try:
                setattr(member, key, int(records[key]))
            except ValueError:
                member.extra.append((f"pax {key}{where}", _text(records[key])))
    if "comment" in records:
        member.comment = _text(records["comment"])
    for key, value in records.items():
        attr = tarcore.xattr(key, value)
        if attr is not None:
            member.xattrs.append(attr)
        elif key not in KNOWN_KEYS:
            member.extra.append((f"pax {key}{where}", appledouble.describe(key, value)))


def member(archive: tarcore.TarArchive, entry: tarcore.TarEntry) -> Member:
    h = entry.header
    kind = kind_of(entry)
    m = Member(entry.name.rstrip("/") or entry.name, kind, entry.size, mode=h.mode & 0o7777,
               uid=h.uid, gid=h.gid, uname=_text(h.uname), gname=_text(h.gname))
    if h.mtime != NEUTRAL_TIME:
        m.times.append(("modified", tarcore.unix_time(h.mtime)))
    if h.atime:
        m.times.append(("accessed (GNU header)", tarcore.unix_time(h.atime)))
    if h.ctime:
        m.times.append(("changed (GNU header)", tarcore.unix_time(h.ctime)))
    _records_into(m, entry.pax, "")
    for aux in entry.aux:
        if aux.mtime and aux.mtime != NEUTRAL_TIME:
            m.times.append((f"modified ({tarcore.TYPE_NAMES[aux.typeflag]})",
                            tarcore.unix_time(aux.mtime)))
        if aux.uname or aux.gname:
            m.extra.append((f"{tarcore.TYPE_NAMES[aux.typeflag]} owner",
                            f"{_text(aux.uname) or '-'}/{_text(aux.gname) or '-'}"))
    if kind in (SYMLINK, HARDLINK):
        m.link = entry.linkname
    head = b""
    base = m.name.rsplit("/", 1)[-1]
    if kind == FILE and (base.startswith("._") or m.name.startswith("__MACOSX/")) \
            and entry.size <= MAX_SPECIAL_READ:
        head = archive.read(entry)
    m.junk = junk_reason(m.name, kind, head)
    if head and appledouble.is_appledouble(head):
        m.apple = appledouble.parse_appledouble(head)
    return m


def members(archive: tarcore.TarArchive) -> list[Member]:
    return [member(archive, e) for e in archive.entries]


def global_members(archive: tarcore.TarArchive) -> list[Member]:
    """Global pax headers and volume labels, as pseudo-members for the report."""
    out = []
    for n, g in enumerate(archive.globals_, 1):
        m = Member(f"(global pax header {n})", "pax global header")
        if g.header.mtime and g.header.mtime != NEUTRAL_TIME:
            m.times.append(("modified (header)", tarcore.unix_time(g.header.mtime)))
        _records_into(m, {k: v for k, v in g.records.items()
                          if k not in ("path", "linkpath", "size")}, "")
        for key in ("path", "linkpath"):
            if key in g.records:
                m.extra.append((f"pax {key}", _text(g.records[key])))
        out.append(m)
    for n, label in enumerate(archive.labels, 1):
        m = Member(f"(volume label {n})", "volume label", comment=_text(label.name))
        if label.mtime and label.mtime != NEUTRAL_TIME:
            m.times.append(("modified", tarcore.unix_time(label.mtime)))
        out.append(m)
    return out


def hidden_ranges(archive: tarcore.TarArchive) -> list[tuple[str, bytes, str]]:
    out = [(h.where, archive.data[h.start:h.end], f"{h.size} bytes at offset {h.start}")
           for h in archive.hidden]
    for e in archive.replaced():
        out.append((f"in an earlier copy of {e.name!r} (replaced by a later entry with the "
                    "same name)", archive.read(e), f"{e.size} bytes at offset {e.data_offset}"))
    return out


def structure_section(archive: tarcore.TarArchive) -> Section:
    section = Section("structure", "TAR structure")
    formats = sorted({e.header.format for e in archive.entries})
    section.add("formats", "Header formats", formats or ["none"])
    if any(e.pax for e in archive.entries) or archive.globals_:
        section.add("pax", "pax headers", "yes")
    section.add("entries", "Entries", len(archive.entries))
    section.add("end_blocks", "End-of-archive blocks", archive.end_blocks)
    for e in archive.entries[:MAX_STRUCTURE]:
        section.add(f"entry.{e.index}", e.name,
                    f"{e.kind}, {e.size} bytes, header at offset {e.header.offset}")
    if len(archive.entries) > MAX_STRUCTURE:
        section.add("more", "More", f"{len(archive.entries) - MAX_STRUCTURE} more not listed")
    return section


def report_archive(archive: tarcore.TarArchive) -> dict:
    found = members(archive)
    warnings = list(archive.warnings)
    hidden = hidden_ranges(archive)
    if hidden:
        total = sum(len(data) for _w, data, _n in hidden)
        warnings.append(f"CRITICAL: {total} bytes of data are hidden in the TAR archive outside "
                        "its files")
    for m in found:
        if m.link is not None:
            problem = link_problem(m)
            if problem is not None:
                warnings.append(f"{problem}; metaex refuses to clean this archive")
        attrs = [k for k, _ in m.xattrs] + ([k for k, _ in m.apple.xattrs]
                                            if m.apple is not None else [])
        if appledouble.WHERE_FROMS in attrs or appledouble.QUARANTINE in attrs:
            warnings.append(f"CRITICAL: {m.name} records where a file was downloaded from")
    sections = [contents_section(found, "TAR")]
    sections += member_sections(found + global_members(archive), hidden=hidden)
    return {"sections": sections, "warnings": warnings, "complete": True,
            "structure": structure_section(archive)}


def parse(data: bytes | bytearray | memoryview) -> dict:
    """What a TAR archive reveals besides its files (see the module docstring).
    Raises MalformedFileError if the archive is damaged or unsafe."""
    return report_archive(tarcore.open_tar(data))


# -- stripping -------------------------------------------------------------------------------

_OUT_TYPES = {FILE: b"0", DIR: tarcore.DIRECTORY, SYMLINK: tarcore.SYMLINK,
              HARDLINK: tarcore.HARDLINK, FIFO: tarcore.FIFO}


def kept_entries(archive: tarcore.TarArchive) -> list[tuple[tarcore.TarEntry, Member]]:
    """(entry, member) of what a cleaned archive keeps: every entry but
    operating system junk and earlier copies replaced by a later entry."""
    replaced = {id(e) for e in archive.replaced()}
    return [(e, m) for e, m in zip(archive.entries, members(archive))
            if not m.junk and id(e) not in replaced]


def check_strippable(kept: list[tuple[tarcore.TarEntry, Member]]) -> None:
    """Raise CannotCleanError for links metaex must not keep."""
    seen: set[str] = set()
    for entry, m in kept:
        if m.link is not None:
            problem = link_problem(m)
            if problem is not None:
                raise CannotCleanError(f"the TAR archive holds a {problem}; this reveals where "
                                       "the files came from, but removing the link would "
                                       "change the archive; refused")
            if m.kind == HARDLINK and tarcore.normalized(m.link) not in seen:
                raise CannotCleanError(f"the hard link {m.name!r} points to {m.link!r}, which "
                                       "is not an earlier file in the cleaned archive; refused")
        seen.add(tarcore.normalized(entry.name))


def out_member(archive: tarcore.TarArchive, entry: tarcore.TarEntry, m: Member
               ) -> tarwriter.OutMember:
    name = entry.raw_name
    if m.kind == DIR and not name.endswith(b"/"):
        name += b"/"
    typeflag = _OUT_TYPES.get(m.kind, entry.typeflag)
    data = archive.read(entry) if m.kind == FILE else b""
    link = entry.raw_linkname if m.kind in (SYMLINK, HARDLINK) else b""
    return tarwriter.OutMember(name, typeflag, neutral_mode(m), data, link,
                               entry.header.devmajor, entry.header.devminor)


def contents_digest(archive: tarcore.TarArchive, entries: list[tarcore.TarEntry]) -> str:
    """SHA-256 over every entry's name, type, link target and data."""
    h = hashlib.sha256()
    for e in entries:
        kind = kind_of(e)
        h.update(tarcore.encode(e.name.rstrip("/") or e.name) + b"\x00"
                 + kind.encode("ascii") + b"\x00" + e.raw_linkname * (kind in (SYMLINK,
                                                                               HARDLINK))
                 + b"\x00" + hashlib.sha256(archive.read(e)).digest())
    return h.hexdigest()


def strip_archive(archive: tarcore.TarArchive) -> bytes:
    kept = kept_entries(archive)
    check_strippable(kept)
    try:
        cleaned = tarwriter.write_tar([out_member(archive, e, m) for e, m in kept])
    except ValueError as exc:
        raise MalformedFileError(f"the cleaned TAR archive can't be written: {exc}") from None
    result = tarcore.open_tar(cleaned)
    if contents_digest(result, result.entries) != contents_digest(archive,
                                                                   [e for e, _m in kept]):
        raise MalformedFileError("internal check failed: the archive's contents changed while "
                                 "stripping")
    return cleaned


def strip(data: bytes | bytearray | memoryview, *, keep_orientation: bool = True,
          paranoid: bool = False) -> bytes:
    """Return a cleaned copy of a TAR archive (whitelist).

    Kept, in order: every file, folder, link, device and fifo entry, with
    its name, link target and data byte for byte. Each gets a new plain
    ustar header: time 1980-01-01 00:00 UTC, owner and group 0 with empty
    names, permissions 644 (755 for folders and executables, 777 for
    symbolic links). A pax header is written only for names or link
    targets too long for ustar, with nothing but the name. Removed: pax
    times, owners, extended attributes and comments, global pax headers,
    GNU volume labels and long-name headers, every unused header byte,
    data in padding and after the end, earlier copies of files replaced by
    a later entry, and operating system junk (AppleDouble "._" files,
    __MACOSX/, .DS_Store, Thumbs.db, desktop.ini). ``keep_orientation``
    and ``paranoid`` apply to files inside, which are not cleaned here.

    Raises MalformedFileError for damaged or unsafe archives,
    UnsupportedFormatError for sparse files and unknown entry types, and
    CannotCleanError for links to absolute paths, outside the archive or
    into a user folder.
    """
    return strip_archive(tarcore.open_tar(data))


def removed_sizes(data: bytes | bytearray | memoryview) -> dict[str, int]:
    """Bytes of hidden data a cleaned archive drops."""
    try:
        archive = tarcore.open_tar(data)
    except MetaexError:
        return {}
    total = sum(len(d) for _w, d, _n in hidden_ranges(archive))
    return {"archive_hidden": total} if total else {}


def strip_warnings(data: bytes, paranoid: bool = False) -> list[str]:
    try:
        archive = tarcore.open_tar(data)
    except MetaexError:
        return []
    return junk_warnings(members(archive))


def kept_warnings(cleaned: bytes) -> list[str]:
    try:
        archive = tarcore.open_tar(cleaned)
    except MetaexError:
        return []
    return user_folder_warnings(members(archive))
