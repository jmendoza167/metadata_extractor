"""Collect the files a command works on from the paths given on the command line.

Files named on the command line are always included; folders are searched
one level deep, or all the way down with ``recursive``. Inside folders some
entries are skipped, and each skipped entry is listed with its reason:

- symbolic links and Windows junctions: never followed;
- version control folders (.git, .hg, .svn), node_modules, __pycache__ and
  Python virtual environments: always skipped;
- hidden files and folders (a name starting with "." or the Windows hidden
  attribute), unless ``include_hidden``;
- metaex's own cleaned copies (``*_clean.*``), when ``skip_clean_outputs``;
- the output folder (``exclude``) and anything inside it;
- devices, pipes and sockets (reading those could block forever);
- subfolders, without ``recursive``.

Every file is listed once, even if several arguments reach it, and the list
is sorted by path so the order is the same on every operating system. The
file system is walked with an explicit stack (no recursion) and links are
never followed, so a crafted folder tree can't make the walk loop.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

ALWAYS_SKIPPED_DIRS = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__"})
"""Folders never searched, even with --include-hidden."""

SYMLINK = "symbolic link (not followed)"
HIDDEN = "hidden (use --include-hidden to include it)"
VCS = "version control folder"
DEPENDENCIES = "dependency or cache folder"
VENV = "Python virtual environment"
CLEAN_OUTPUT = "metaex output (*_clean.*)"
OUTPUT_FOLDER = "output folder"
NOT_REGULAR = "not a regular file"
SUBFOLDER = "folder (use --recursive to include it)"

_REPARSE_SYMLINK = 0xA000000C      # IO_REPARSE_TAG_SYMLINK
_REPARSE_MOUNT_POINT = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT (junctions)
_FILE_ATTRIBUTE_HIDDEN = 0x2
_CLEAN_NAME = re.compile(r".+_clean(\.[^.]+){1,2}", re.DOTALL)
_WINDOWS_LONG_PATH = 240
"""Paths at least this long get the \\\\?\\ prefix on Windows, so they work
even where long path support is not enabled."""


@dataclass(frozen=True)
class Item:
    path: str
    """Path used to open the file and shown to the user."""
    relative: tuple[str, ...]
    """Path inside the folder argument it was found in (for a file named on
    the command line: just its name), as parts."""
    explicit: bool
    """True if the file was named on the command line."""
    root: str | None = None
    """The folder argument it was found in; None for a file argument."""

    @property
    def relative_text(self) -> str:
        return "/".join(self.relative)


@dataclass(frozen=True)
class Skipped:
    path: str
    reason: str


@dataclass
class Collection:
    items: list[Item] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    """Entries left out on purpose, with the reason."""
    errors: list[Skipped] = field(default_factory=list)
    """Paths that could not be read: a missing argument, an unreadable folder."""


def is_batch(paths: Sequence[str]) -> bool:
    """True unless ``paths`` is a single path that is not a folder (that
    path keeps the single-file behavior and output)."""
    if len(paths) != 1:
        return True
    try:
        st = os.lstat(paths[0])
    except OSError:
        return False  # the single-file code reports the error
    return stat.S_ISDIR(st.st_mode) and not is_link(st)


def is_clean_output(name: str) -> bool:
    """True for names metaex gives cleaned copies: photo_clean.jpg,
    backup_clean.tar.gz."""
    return _CLEAN_NAME.fullmatch(name) is not None


def is_link(st: os.stat_result) -> bool:
    """A symbolic link, or on Windows a symbolic link or junction (other
    reparse points, such as cloud placeholder files, are ordinary files)."""
    if stat.S_ISLNK(st.st_mode):
        return True
    return getattr(st, "st_reparse_tag", 0) in (_REPARSE_SYMLINK, _REPARSE_MOUNT_POINT)


def is_hidden(name: str, st: os.stat_result) -> bool:
    if name.startswith("."):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_HIDDEN)


def path_key(path: str) -> str:
    """A key that is equal for two spellings of the same path: absolute,
    links in the folders above resolved and, on Windows and macOS (whose
    file systems ignore case by default), case-folded."""
    key = os.path.realpath(os.path.abspath(path))
    if sys.platform in ("win32", "darwin"):
        key = key.casefold()
    return os.path.normcase(key)


def same_path(a: str, b: str) -> bool:
    return path_key(a) == path_key(b)


def is_inside(path: str, folder: str) -> bool:
    """True if ``path`` is ``folder`` or anything inside it."""
    p, f = path_key(path), path_key(folder)
    return p == f or p.startswith(f.rstrip(os.sep) + os.sep)


def io_path(path: str) -> str:
    """``path``, with the \\\\?\\ prefix on Windows when it is long."""
    if sys.platform != "win32" or path.startswith("\\\\"):
        return path
    full = os.path.abspath(path)
    return "\\\\?\\" + full if len(full) >= _WINDOWS_LONG_PATH else path


def native(path: str) -> str:
    """``path`` with the system's own separator: on Windows "/" becomes "\\",
    so paths built from it don't mix both (elsewhere it is unchanged)."""
    return path.replace("/", "\\") if sys.platform == "win32" else path


def display_path(path: str) -> str:
    """``path`` without the long path prefix io_path() may have added."""
    return path[4:] if sys.platform == "win32" and path.startswith("\\\\?\\") else path


def collect(paths: Iterable[str], *, recursive: bool = False, include_hidden: bool = False,
            skip_clean_outputs: bool = True, exclude: Iterable[str] = ()) -> Collection:
    """The files to work on, what was skipped and what could not be read."""
    result = Collection()
    excluded = {path_key(p) for p in exclude}
    seen: set[object] = set()
    for arg in paths:
        _collect_argument(native(os.fspath(arg)), result, seen, excluded, recursive=recursive,
                          include_hidden=include_hidden, skip_clean_outputs=skip_clean_outputs)
    result.items.sort(key=lambda item: sort_key(item.path))
    result.skipped.sort(key=lambda s: sort_key(s.path))
    result.errors.sort(key=lambda s: sort_key(s.path))
    return result


def _collect_argument(arg: str, result: Collection, seen: set[object], excluded: set[str], *,
                      recursive: bool, include_hidden: bool, skip_clean_outputs: bool) -> None:
    try:
        st = os.lstat(io_path(arg))
    except FileNotFoundError:
        result.errors.append(Skipped(arg, "no such file or folder"))
        return
    except OSError as exc:
        result.errors.append(Skipped(arg, f"cannot access it ({_reason(exc)})"))
        return
    if is_link(st):
        result.errors.append(Skipped(arg, "is a symbolic link; metaex never follows links "
                                          "(name the file or folder it points to)"))
        return
    if stat.S_ISREG(st.st_mode):
        _add(result, seen, Item(io_path(arg), (os.path.basename(arg),), explicit=True))
        return
    if not stat.S_ISDIR(st.st_mode):
        result.errors.append(Skipped(arg, NOT_REGULAR))
        return
    _walk(arg, result, seen, excluded, recursive=recursive, include_hidden=include_hidden,
          skip_clean_outputs=skip_clean_outputs)


def _walk(root: str, result: Collection, seen: set[object], excluded: set[str], *,
          recursive: bool, include_hidden: bool, skip_clean_outputs: bool) -> None:
    stack: list[tuple[str, tuple[str, ...]]] = [(root, ())]
    while stack:
        folder, parts = stack.pop()
        try:
            with os.scandir(io_path(folder)) as entries:
                listing = sorted(entries, key=lambda e: e.name)
        except OSError as exc:
            result.errors.append(Skipped(folder, f"cannot read the folder ({_reason(exc)})"))
            continue
        subfolders = []
        for entry in listing:
            path = os.path.join(folder, entry.name)
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                result.errors.append(Skipped(path, f"cannot access it ({_reason(exc)})"))
                continue
            reason = _skip_reason(entry.name, path, st, excluded, recursive=recursive,
                                  include_hidden=include_hidden,
                                  skip_clean_outputs=skip_clean_outputs)
            if reason is not None:
                result.skipped.append(Skipped(path, reason))
            elif stat.S_ISDIR(st.st_mode):
                subfolders.append((path, parts + (entry.name,)))
            else:
                _add(result, seen, Item(io_path(path), parts + (entry.name,), explicit=False,
                                        root=root))
        stack.extend(reversed(subfolders))


def _skip_reason(name: str, path: str, st: os.stat_result, excluded: set[str], *,
                 recursive: bool, include_hidden: bool, skip_clean_outputs: bool) -> str | None:
    if is_link(st):
        return SYMLINK
    is_dir = stat.S_ISDIR(st.st_mode)
    if is_dir:
        if name in (".git", ".hg", ".svn"):
            return VCS
        if name in ALWAYS_SKIPPED_DIRS:
            return DEPENDENCIES
        if _is_venv(path):
            return VENV
    if not include_hidden and is_hidden(name, st):
        return HIDDEN
    if is_dir:
        if excluded and path_key(path) in excluded:
            return OUTPUT_FOLDER
        return None if recursive else SUBFOLDER
    if not stat.S_ISREG(st.st_mode):
        return NOT_REGULAR
    if skip_clean_outputs and is_clean_output(name):
        return CLEAN_OUTPUT
    return None


def _is_venv(path: str) -> bool:
    return (os.path.isfile(io_path(os.path.join(path, "pyvenv.cfg")))
            or os.path.isdir(io_path(os.path.join(path, "conda-meta"))))


def _add(result: Collection, seen: set[object], item: Item) -> None:
    key = _identity(item.path)
    if key in seen:
        return
    seen.add(key)
    result.items.append(item)


def _identity(path: str) -> object:
    """The same file reached twice has the same identity: its device and
    file number where the system has them, else its resolved path."""
    try:
        st = os.lstat(path)
    except OSError:
        return path_key(path)
    return (st.st_dev, st.st_ino) if st.st_ino else path_key(path)


def sort_key(path: str) -> tuple[str, ...]:
    """Sort by path parts, so "a/b" and "a-b" compare the same everywhere."""
    path = display_path(path)
    if os.altsep:
        path = path.replace(os.altsep, os.sep)
    return tuple(path.split(os.sep))


def _reason(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__
