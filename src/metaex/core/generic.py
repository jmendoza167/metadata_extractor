"""Metadata every file has, regardless of format: what the file system knows."""

from __future__ import annotations

import os
import stat
import sys
from datetime import datetime, timezone

from metaex.core.errors import FileAccessError
from metaex.core.loader import format_size
from metaex.core.result import Section

UNAVAILABLE = "unavailable"


def file_system_section(path: str | os.PathLike[str]) -> Section:
    """Size, timestamps and permissions as reported by the operating system."""
    name = os.fspath(path)
    try:
        st = os.stat(name)
    except OSError as exc:
        raise FileAccessError(f"{name}: cannot read file information ({exc.strerror or exc})") from None

    section = Section("file_system", "File system")
    section.add("path", "Path", name)
    section.add("size_bytes", "Size (bytes)", st.st_size)
    section.add("size", "Size", format_size(st.st_size))
    _add_time(section, "modified", "Modified", st.st_mtime)
    _add_time(section, "accessed", "Accessed", st.st_atime)

    created = _creation_time(st)
    if created is None:
        section.add("created", "Created", UNAVAILABLE,
                    note="this operating system does not report creation time")
    else:
        _add_time(section, "created", "Created", created)

    note = "Windows only reports the read-only flag" if sys.platform == "win32" else None
    section.add("permissions", "Permissions",
                f"{stat.filemode(st.st_mode)} ({stat.S_IMODE(st.st_mode):o})", note=note)
    return section


def _creation_time(st: os.stat_result) -> float | None:
    birth = getattr(st, "st_birthtime", None)
    if birth is not None:
        return birth
    if sys.platform == "win32":
        # Before Python 3.12, Windows has no st_birthtime and st_ctime is
        # documented to be the creation time there.
        return st.st_ctime
    # On Linux st_ctime is the inode *change* time, not creation; don't guess.
    return None


def to_local_datetime(timestamp: float) -> datetime | None:
    """Convert a POSIX timestamp to an aware local datetime (whole seconds)."""
    try:
        utc = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return utc.astimezone().replace(microsecond=0)
    except (OverflowError, OSError, ValueError):
        return None


def _add_time(section: Section, key: str, label: str, timestamp: float) -> None:
    value = to_local_datetime(timestamp)
    if value is None:
        section.add(key, label, UNAVAILABLE, note=f"invalid timestamp {timestamp!r}")
    else:
        section.add(key, label, value)
