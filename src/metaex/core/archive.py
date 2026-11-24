"""What an archive (ZIP, TAR) reveals besides the files in it.

Each archive format turns its entries into ``Member`` objects; the report
sections are built here, the same way for every format:

- archive_contents: how many entries of each kind (not metadata)
- archive_hidden: data that belongs to no entry
- archive_comments: the archive comment and entry comments
- archive_times: entry times (other than metaex's neutral 1980-01-01 00:00)
- archive_owners: owner user and group names and numbers (other than 0)
- archive_system: the operating system that made the entries (other than
  MS-DOS and Unix, which nearly every tool writes) and permissions other
  than the neutral 644 (755 for folders and executables)
- archive_junk: files the operating system adds by itself: macOS
  ``__MACOSX/`` folders, AppleDouble ``._`` files, ``.DS_Store``, Windows
  ``Thumbs.db`` and ``desktop.ini``
- archive_xattrs: extended attributes (from AppleDouble files and TAR
  headers), such as the quarantine record and the "where from" URLs of a
  download
- archive_paths: entry names that reveal a user folder (home/<name>/...)
- archive_links: symbolic and hard link targets
- archive_extra: other recorded fields (unknown extra fields or keys)
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from metaex.core import appledouble
from metaex.core.paths import local_paths, user_name
from metaex.core.result import Section

FILE, DIR, SYMLINK, HARDLINK, DEVICE, FIFO = "file", "folder", "symlink", "hard link", \
    "device", "fifo"

MAX_LISTED = 50
"""At most this many fields per report section; the rest are counted."""

NEUTRAL_TIME = 315532800
"""1980-01-01T00:00:00Z: the time metaex gives every entry it writes."""
NEUTRAL_FILE_MODE = 0o644
NEUTRAL_EXEC_MODE = 0o755
NEUTRAL_DIR_MODE = 0o755
NEUTRAL_LINK_MODE = 0o777


@dataclass
class Member:
    name: str
    """Path inside the archive, without a trailing "/" for folders."""
    kind: str = FILE
    size: int = 0
    mode: int | None = None
    """Permission bits (0o7777), None if the archive doesn't record them."""
    uid: int | None = None
    gid: int | None = None
    uname: str = ""
    gname: str = ""
    times: list[tuple[str, str]] = field(default_factory=list)
    """(what, when) of every recorded time, e.g. ("modified", "2024-05-01T10:00:00")."""
    link: str | None = None
    xattrs: list[tuple[str, bytes]] = field(default_factory=list)
    comment: str = ""
    extra: list[tuple[str, str]] = field(default_factory=list)
    """Other recorded fields: (label, value)."""
    system: str | None = None
    """Operating system that made the entry (ZIP "version made by")."""
    junk: str | None = None
    """Why this entry is an operating system junk file, if it is one."""
    apple: appledouble.AppleDouble | None = None
    """The attributes held by an AppleDouble ("._") entry."""


def neutral_mode(member: Member) -> int:
    if member.kind == DIR:
        return NEUTRAL_DIR_MODE
    if member.kind == SYMLINK:
        return NEUTRAL_LINK_MODE
    if member.kind == FILE and member.mode is not None and member.mode & 0o111:
        return NEUTRAL_EXEC_MODE
    return NEUTRAL_FILE_MODE


# -- operating system junk ------------------------------------------------------------


MACOSX_FOLDER = "__MACOSX"


def junk_reason(name: str, kind: str, head: bytes = b"") -> str | None:
    """Why an entry is a file the operating system added by itself, else None.

    ``head`` is the start of the entry's contents: a "._" file is junk only
    if it really is an AppleDouble file.
    """
    parts = name.split("/")
    base = parts[-1]
    if kind == FILE and base.startswith("._") and appledouble.is_appledouble(head):
        return "macOS AppleDouble file: extended attributes of " + apple_target(name)
    if parts[0] == MACOSX_FOLDER:
        return ("macOS AppleDouble data (__MACOSX folder): extended attributes and resource "
                "forks of the files")
    if kind == FILE and base == ".DS_Store":
        return "macOS Finder folder settings (.DS_Store; can list files that were in the folder)"
    if kind == FILE and base.lower() == "thumbs.db":
        return ("Windows thumbnail cache (Thumbs.db; can hold thumbnails of pictures that were "
                "in the folder)")
    if kind == FILE and base.lower() == "desktop.ini":
        return "Windows folder settings (desktop.ini)"
    return None


def apple_target(name: str) -> str:
    """The file an AppleDouble entry belongs to: __MACOSX/a/._b -> a/b."""
    parts = name.split("/")
    if parts[0] == MACOSX_FOLDER:
        parts = parts[1:]
    if parts and parts[-1].startswith("._"):
        parts[-1] = parts[-1][2:]
    return "/".join(parts) or name


# -- user folders and links ----------------------------------------------------------------

_HOME = re.compile(r"(?i)^(?:\./)?(?:home|users|documents and settings)/([^/]+)/")


def name_user(name: str) -> str | None:
    """The user name in an entry name that starts with a user folder
    (home/jane/..., Users/jane/...): archivers drop the leading "/"."""
    m = _HOME.match(name)
    if m is None:
        return None
    user = m.group(1)
    return None if user.lower() in ("shared", "public", "default", "all users") else user


def link_user(target: str) -> str | None:
    """The user name a link target reveals (absolute or archive-relative)."""
    for path in local_paths(target) or [target]:
        found = user_name(path)
        if found:
            return found
    return name_user(target.lstrip("/"))


def link_problem(member: Member) -> str | None:
    """Why a link may not be kept: an absolute target, or one that leaves
    the archive's top folder, or one in a user folder. None if it is fine."""
    target = member.link or ""
    if not target:
        return f"{member.kind} {member.name!r} has an empty target"
    if target.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", target) or "\\" in target:
        return f"{member.kind} {member.name!r} points to the absolute path {target!r}"
    if member.kind == HARDLINK:
        base = target
    else:
        base = posixpath.join(posixpath.dirname(member.name), target)
    depth = 0
    for part in base.split("/"):
        if part == "..":
            depth -= 1
            if depth < 0:
                return f"{member.kind} {member.name!r} points outside the archive ({target!r})"
        elif part not in ("", "."):
            depth += 1
    user = link_user(target)
    if user:
        return f"{member.kind} {member.name!r} points into the user folder of {user!r}"
    return None


# -- report ----------------------------------------------------------------------------------


def _limited(section: Section, total: int) -> None:
    if total > MAX_LISTED:
        section.add("more", "More", f"{total - MAX_LISTED} more not listed")


def _counted(values: list[str]) -> str:
    """"644 (3 entries), 600 (1 entry)" from a list of values, in first-seen order."""
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return ", ".join(f"{v} ({n} {'entry' if n == 1 else 'entries'})" for v, n in counts.items())


def contents_section(members: list[Member], format_name: str) -> Section:
    section = Section("archive_contents", "Archive contents")
    section.add("format", "Format", format_name)
    section.add("entries", "Entries", len(members))
    kinds: dict[str, int] = {}
    for m in members:
        kinds[m.kind] = kinds.get(m.kind, 0) + 1
    for kind, n in kinds.items():
        section.add(f"count.{kind}", f"{kind.capitalize()}s", n)
    section.add("size", "Uncompressed size (bytes)", sum(m.size for m in members
                                                         if m.kind == FILE))
    return section


def member_sections(members: list[Member], *, comment: str = "",
                    hidden: list[tuple[str, bytes, str]] = ()) -> list[Section]:
    """The metadata sections for ``members`` (see the module docstring).
    ``hidden`` is (where, data, note) for each range of hidden data."""
    sections: list[Section] = []

    s = Section("archive_hidden", "Hidden data in the archive")
    for n, (where, data, note) in enumerate(hidden, 1):
        s.add(f"range_{n}", f"Data {where}", data, sensitive=True, note=note)
    sections.append(s)

    s = Section("archive_comments", "Archive comments")
    if comment:
        s.add("archive_comment", "Archive comment", comment, sensitive=True)
    commented = [m for m in members if m.comment]
    for m in commented[:MAX_LISTED]:
        s.add(f"comment.{m.name}", f"Comment on {m.name}", m.comment, sensitive=True)
    _limited(s, len(commented))
    sections.append(s)

    s = Section("archive_times", "Entry times")
    timed = [m for m in members if m.times]
    for m in timed[:MAX_LISTED]:
        s.add(f"times.{m.name}", m.name, "; ".join(f"{what} {when}" for what, when in m.times),
              sensitive=True)
    _limited(s, len(timed))
    sections.append(s)

    s = Section("archive_owners", "Entry owners")
    names = [f"{m.uname or '-'}/{m.gname or '-'}" for m in members if m.uname or m.gname]
    if names:
        s.add("owner_names", "Owner user/group names", _counted(names), sensitive=True)
    ids = [f"{m.uid if m.uid is not None else '-'}/{m.gid if m.gid is not None else '-'}"
           for m in members if m.uid or m.gid]
    if ids:
        s.add("owner_ids", "Owner user/group IDs", _counted(ids), sensitive=True)
    sections.append(s)

    s = Section("archive_system", "System information")
    systems = [m.system for m in members if m.system and m.system not in ("MS-DOS", "Unix")]
    if systems:
        s.add("created_by", "Created on", _counted(systems))
    modes = [f"{m.mode:o} ({m.kind})" for m in members
             if m.mode is not None and m.kind != SYMLINK and m.mode != neutral_mode(m)]
    if modes:
        s.add("permissions", "Permissions", _counted(modes))
    sections.append(s)

    s = Section("archive_junk", "Operating system files")
    junk = [m for m in members if m.junk]
    for m in junk[:MAX_LISTED]:
        s.add(f"junk.{m.name}", m.name, m.junk, sensitive=True)
    _limited(s, len(junk))
    sections.append(s)

    s = Section("archive_xattrs", "Extended attributes")
    attrs: list[tuple[str, str, bytes]] = []
    for m in members:
        attrs += [(m.name, k, v) for k, v in m.xattrs]
        if m.apple is not None:
            target = apple_target(m.name)
            attrs += [(target, k, v) for k, v in m.apple.xattrs]
            attrs += [(target, k, v.encode("utf-8")) for k, v in m.apple.other]
    for owner, name, value in attrs[:MAX_LISTED]:
        s.add(f"xattr.{owner}.{name}", f"{owner}: {name}", appledouble.describe(name, value),
              sensitive=True)
    _limited(s, len(attrs))
    sections.append(s)

    s = Section("archive_paths", "Names that reveal a user folder")
    revealing = [(m, name_user(m.name)) for m in members]
    revealing = [(m, u) for m, u in revealing if u]
    for m, user in revealing[:MAX_LISTED]:
        s.add(f"name.{m.name}", m.name, f"user folder of {user!r}", sensitive=True)
    _limited(s, len(revealing))
    sections.append(s)

    s = Section("archive_links", "Link targets")
    linked = [m for m in members if m.link is not None]
    for m in linked[:MAX_LISTED]:
        s.add(f"link.{m.name}", f"{m.name} ({m.kind})", m.link, sensitive=True,
              note=link_problem(m))
    _limited(s, len(linked))
    sections.append(s)

    s = Section("archive_extra", "Other entry information")
    extra = [(m, label, value) for m in members for label, value in m.extra]
    for n, (m, label, value) in enumerate(extra[:MAX_LISTED]):
        s.add(f"extra.{m.name}.{n}", f"{m.name}: {label}", value)
    _limited(s, len(extra))
    sections.append(s)

    return [s for s in sections if s.fields]
