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
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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


def name_user(name: str, is_dir: bool = False) -> str | None:
    """The user name in an entry name that starts with a user folder
    (home/jane/..., Users/jane/..., or the folder home/jane itself):
    archivers drop the leading "/"."""
    m = _HOME.match(name + "/" if is_dir and not name.endswith("/") else name)
    if m is None:
        return None
    user = m.group(1)
    return None if user.lower() in ("shared", "public", "default", "all users") else user


def link_user(target: str) -> str | None:
    """The user name an absolute link target reveals (/home/<name>/...,
    C:\\Users\\<name>\\...). A relative target points inside the archive,
    whose names are reported on their own."""
    for path in local_paths(target) or [target]:
        found = user_name(path)
        if found:
            return found
    return None


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


# -- warnings for strip ------------------------------------------------------------------------

MAX_LISTED_WARNING = 20


def some(names: list[str]) -> str:
    """The first names of a list, and how many more there are."""
    listed = ", ".join(names[:MAX_LISTED_WARNING])
    more = len(names) - MAX_LISTED_WARNING
    return listed + (f" and {more} more" if more > 0 else "")


def junk_warnings(members: list[Member]) -> list[str]:
    """The operating system files a cleaned archive leaves out, by name."""
    junk = [m.name for m in members if m.junk]
    return [f"removed {len(junk)} operating system file(s): {some(junk)}"] if junk else []


def user_folder_warnings(members: list[Member]) -> list[str]:
    """Entry names that reveal a user folder: kept, since they are the files'
    names, so the user must know."""
    users = sorted({u for u in (name_user(m.name, m.kind == DIR) for m in members) if u})
    if not users:
        return []
    return [f"CRITICAL: entry names reveal the user folder of {', '.join(map(repr, users))} "
            "(home/<name>/... or Users/<name>/...); metaex keeps file names as they are. Move "
            "the files out of the user folder and create the archive again to remove it"]


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
    revealing = [(m, name_user(m.name, m.kind == DIR)) for m in members]
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


# -- files inside archives ---------------------------------------------------------------------

MAX_NESTING = 3
"""Archives inside archives are read up to this depth (the file itself is 0)."""
MAX_INNER_FILES = 1000
"""Files inside examined at most, counting every level of nesting."""
ARCHIVE_KEYS = ("zip", "tar", "gzip", "bzip2", "xz")
NOT_REPEATED = {"archive_contents", "compress_contents", "structure"}
"""Sections of an inner file that describe it, not its metadata."""


@dataclass
class _Counter:
    left: int


@dataclass(frozen=True)
class Scan:
    depth: int = 0
    inner: bool = True
    """Whether files inside archives are read at all."""
    counter: _Counter = field(default_factory=lambda: _Counter(MAX_INNER_FILES))
    prefix: str = ""
    """Path of the archive being read inside the outer one ("a.zip > ")."""


_SCAN: ContextVar[Scan | None] = ContextVar("metaex_archive_scan", default=None)


def scan_state() -> Scan:
    state = _SCAN.get()
    return state if state is not None else Scan()


@contextmanager
def scan_inner(enabled: bool) -> Iterator[None]:
    """Read the files inside archives (show, risk, verify) or not (strip's
    check of the archive it just wrote: files it didn't clean are warned
    about instead)."""
    state = scan_state()
    token = _SCAN.set(Scan(state.depth, enabled, state.counter, state.prefix))
    try:
        yield
    finally:
        _SCAN.reset(token)


@contextmanager
def inside(path: str) -> Iterator[Scan]:
    """Read (or clean) the file ``path`` one level deeper."""
    state = scan_state()
    token = _SCAN.set(Scan(state.depth + 1, state.inner, state.counter,
                           f"{state.prefix}{path} > "))
    try:
        yield _SCAN.get()
    finally:
        _SCAN.reset(token)


@contextmanager
def own_budget() -> Iterator[None]:
    """Read or clean one file inside on a decompression budget of its own,
    cut from the shared one: a bomb inside uses up only its own budget (the
    file is then reported, or copied as it is), and only what was really
    produced is charged to the shared budget."""
    from metaex.core.inflate import Budget, shared_budget, sharing

    shared = shared_budget()
    if shared is None:
        yield
        return
    child = Budget(shared.remaining)
    try:
        with sharing(child):
            yield
    finally:
        shared.remaining = max(0, shared.remaining - child.spent)


@contextmanager
def root_scan() -> Iterator[None]:
    """Start counting the files inside (MAX_INNER_FILES) here, unless an
    outer archive already does."""
    if _SCAN.get() is not None:
        yield
        return
    token = _SCAN.set(Scan())
    try:
        yield
    finally:
        _SCAN.reset(token)


@dataclass
class InnerFile:
    path: str
    kind: str
    """The detected type's name."""
    status: str
    """"examined", "partly examined" or why it was not examined."""
    leftovers: int = 0
    """Metadata fields a cleaned file of its type must not have."""
    unchecked: list[str] = field(default_factory=list)
    """What metaex could not examine inside this file (files inside a
    nested archive, embedded objects in a document)."""


UNRECOGNIZED = "not examined: type not recognized"


def is_unchecked(status: str) -> bool:
    """True if a file inside was not examined although it may hold metadata:
    past the limits, of a type metaex doesn't read, or unreadable (a file
    whose type isn't recognized at all is plain content)."""
    return status.startswith("not examined") and status != UNRECOGNIZED


def unchecked_files(found: list[InnerFile]) -> list[str]:
    """Every file inside (at any depth) that metaex could not examine."""
    out: list[str] = []
    for f in found:
        if is_unchecked(f.status):
            out.append(f"{f.path} ({f.kind}): {f.status}")
        out += [f"{f.path} > {item}" for item in f.unchecked]
    return out


def _not_examined(state: Scan, detected) -> str | None:
    from metaex.core import filetype

    if detected.key == filetype.UNKNOWN.key:
        return UNRECOGNIZED
    if detected.key in ARCHIVE_KEYS and state.depth + 1 > MAX_NESTING:
        return f"not examined: archives are read only {MAX_NESTING} levels deep"
    if state.counter.left <= 0:
        return f"not examined: more than {MAX_INNER_FILES} files inside"
    return None


def inner_sections(files: list[tuple[str, bytes]], warnings: list[str]
                   ) -> tuple[list[Section], list[InnerFile]]:
    """The metadata of the files inside an archive, read with their own
    format's parser: their sections (titles start with the file's path;
    each keeps its key, so its risk level, and the rule of what a cleaned
    file of its type may keep), and what was found for each file. Nothing
    if reading inside archives is turned off (see scan_inner())."""
    from metaex.core import filetype
    from metaex.core.errors import MetaexError
    from metaex.core.registry import format_module
    from metaex.core.verify import kept_rule

    state = scan_state()
    if not state.inner:
        return [], []
    sections: list[Section] = []
    found: list[InnerFile] = []
    not_read: list[str] = []
    for path, data in files:
        detected = filetype.detect(data)
        kind = detected.name if detected.key != filetype.UNKNOWN.key else "unrecognized"
        module = format_module(detected)
        reason = _not_examined(state, detected)
        if reason is None and module is None:
            reason = f"not examined: metaex doesn't read {detected.name} files"
        if reason is not None:
            found.append(InnerFile(path, kind, reason))
            if is_unchecked(reason):
                not_read.append(f"{path} ({kind}; {reason[len('not examined: '):]})")
            continue
        state.counter.left -= 1
        try:
            with inside(path), own_budget():
                parsed = module.parse(data)
        except MetaexError as exc:
            found.append(InnerFile(path, kind, f"not examined: {exc}"))
            warnings.append(f"{path} ({kind}) inside the archive could not be examined: {exc}")
            continue
        rule = kept_rule(module)
        count = 0
        for s in parsed.get("sections", []):
            if s.key in NOT_REPEATED:
                continue
            kept = s.kept if s.kept is not None else (
                lambda f, key=s.key, rule=rule: rule(key, f))
            sections.append(Section(s.key, f"{path}: {s.title}", s.fields, kept=kept))
            count += sum(1 for f in s.fields if not kept(f))
        for w in parsed.get("warnings", []):
            if w.startswith("CRITICAL: "):
                warnings.append(f"CRITICAL: {path}: {w[len('CRITICAL: '):]}")
            else:
                warnings.append(f"{path}: {w}")
        status = "examined" if parsed.get("complete", True) else "partly examined"
        nested = list(parsed.get("unchecked", [])) + unchecked_files(parsed.get("inner", []))
        found.append(InnerFile(path, kind, status, count, nested))
    if not_read:
        warnings.append(f"{len(not_read)} file(s) inside were not examined and may contain "
                        f"metadata: {some(not_read)}")
    return sections, found


def files_section(found: list[InnerFile]) -> Section | None:
    """What was found inside: not metadata itself (risk leaves it out, and a
    cleaned archive may keep it)."""
    if not found:
        return None
    s = Section("archive_files", "Files inside", kept=lambda f: True)
    for n, item in enumerate(found[:MAX_LISTED]):
        if item.status.startswith("not examined"):
            text = f"{item.kind}; {item.status}"
        elif item.leftovers:
            text = f"{item.kind}; {item.leftovers} metadata field(s)"
        else:
            text = f"{item.kind}; no metadata"
        s.add(f"file_{n + 1}", item.path, text)
    _limited(s, len(found))
    return s


def scan_files(files: list[tuple[str, bytes]], warnings: list[str]
               ) -> tuple[list[Section], list[InnerFile]]:
    """inner_sections() plus the "Files inside" overview, first."""
    sections, found = inner_sections(files, warnings)
    overview = files_section(found)
    return ([overview] if overview is not None else []) + sections, found


def inner_warnings(found: list[InnerFile]) -> list[str]:
    """For strip: files inside that still hold metadata (not cleaned without
    --deep, or files --deep had to copy as they were)."""
    left = [f for f in found if f.leftovers]
    if not left:
        return []
    listed = some([f"{f.path} ({f.kind})" for f in left])
    if deep_session() is not None:
        how = "--deep could not clean them (see the warning about copied files)"
    else:
        how = "they were NOT cleaned; use --deep to clean the files inside the archive too"
    return [f"CRITICAL: {len(left)} file(s) inside the archive still contain metadata: "
            f"{listed}; {how}"]


# -- cleaning files inside (--deep) --------------------------------------------------------------


@dataclass
class DeepSession:
    cleaned: list[tuple[str, str]] = field(default_factory=list)
    """(path, type) of the files inside that were cleaned."""
    copied: list[tuple[str, str]] = field(default_factory=list)
    """(path, reason) of the files inside copied unchanged."""


_DEEP: ContextVar[DeepSession | None] = ContextVar("metaex_deep_session", default=None)


def deep_session() -> DeepSession | None:
    return _DEEP.get()


@contextmanager
def deep_cleaning() -> Iterator[DeepSession]:
    """Collect what --deep did to the files inside (for strip's summary)."""
    session = DeepSession()
    token = _DEEP.set(session)
    try:
        yield session
    finally:
        _DEEP.reset(token)


def _why_not(detected, module) -> str:
    from metaex.core import filetype

    if detected.key == filetype.UNKNOWN.key:
        return "type not recognized"
    reason = filetype.refusal(detected)
    if reason is not None:
        return reason
    return f"metaex can't clean {detected.name} files"


def clean_inner(path: str, data: bytes, *, keep_orientation: bool = True,
                paranoid: bool = False) -> bytes:
    """A file inside an archive, cleaned with its own format's stripper and
    verified against the original (no metadata left, same image, audio or
    contents), for --deep. Files metaex can't clean (unsupported, damaged,
    nested too deep, or not verified) are returned unchanged and listed in
    the deep session; nothing is dropped silently."""
    from metaex.core import filetype
    from metaex.core.errors import MetaexError
    from metaex.core.registry import can_strip, format_module
    from metaex.core.verify import verify

    session = deep_session()
    state = scan_state()
    shown = f"{state.prefix}{path}"

    def copied(reason: str) -> bytes:
        if session is not None:
            session.copied.append((shown, reason))
        return data

    if not data:
        return data
    detected = filetype.detect(data)
    module = format_module(detected)
    if detected.key in ARCHIVE_KEYS and state.depth + 1 > MAX_NESTING:
        return copied(f"archives are cleaned only {MAX_NESTING} levels deep")
    if not can_strip(module):
        return copied(_why_not(detected, module))
    if state.counter.left <= 0:
        return copied(f"more than {MAX_INNER_FILES} files inside")
    state.counter.left -= 1
    options = {"keep_orientation": keep_orientation, "paranoid": paranoid}
    if getattr(module, "CAN_DEEP", False):
        options["deep"] = True
    try:
        with inside(path), own_budget():
            cleaned = module.strip(data, **options)
            with scan_inner(False):
                result = verify(cleaned, data)
    except MetaexError as exc:
        return copied(str(exc))
    # What could not be checked inside it (an embedded object, a nested
    # archive past the limits) is reported when the archive is verified.
    if not result.checked_clean or result.image_data_match is not True:
        reasons = result.problems + [str(f) for f in result.leftovers]
        return copied("the cleaned copy could not be verified (" + "; ".join(reasons[:2]) + ")")
    if cleaned != data and session is not None:
        session.cleaned.append((shown, detected.name))
    return cleaned


def compare_inner(pairs: list[tuple[str, bytes, bytes]]) -> tuple[list[str], list[str]]:
    """Each file of a cleaned archive against the original's: it must be the
    same bytes, or a cleaned copy that verifies against the original file
    (what --deep writes). Returns (problems, paths of verified cleaned copies)."""
    from metaex.core.errors import MetaexError
    from metaex.core.verify import verify

    problems: list[str] = []
    verified: list[str] = []
    for path, mine, theirs in pairs:
        if mine == theirs:
            continue
        try:
            with inside(path), scan_inner(False):
                result = verify(mine, theirs)
        except MetaexError as exc:
            problems.append(f"{path} differs from the original and is not a cleaned copy of "
                            f"it ({exc})")
            continue
        if not result.checked_clean or result.image_data_match is not True:
            reasons = result.problems + [str(f) for f in result.leftovers]
            if result.image_data_match is False:
                reasons.insert(0, f"{result.image_label.lower()} differs")
            problems.append(f"{path} differs from the original and is not a verified cleaned "
                            f"copy of it ({'; '.join(reasons[:2]) or 'not compared'})")
            continue
        verified.append(path)
    return problems, verified


def deep_warnings(session: DeepSession) -> list[str]:
    """For strip's summary: the files --deep copied unchanged, with why."""
    if not session.copied:
        return []
    listed = some([f"{path} ({reason})" for path, reason in session.copied])
    return [f"--deep copied {len(session.copied)} file(s) inside unchanged, because metaex "
            f"can't clean them: {listed}"]
