"""Metadata every file has, regardless of format: what the file system knows."""

from __future__ import annotations

import hashlib
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


# -- Windows "Mark of the Web" ------------------------------------------------------

ZONE_IDENTIFIER_STREAM = "Zone.Identifier"
ZONE_IDENTIFIER_MAX_SIZE = 64 * 1024
ZONE_IDENTIFIER_MAX_ENTRIES = 100

_ZONE_NAMES = {
    "0": "Local machine",
    "1": "Local intranet",
    "2": "Trusted sites",
    "3": "Internet",
    "4": "Restricted sites",
}
_ZONE_LABELS = {
    "zoneid": ("zone_id", "Zone"),
    "referrerurl": ("referrer_url", "Referrer URL"),
    "hosturl": ("host_url", "Host URL"),
}


def mark_of_the_web_section(path: str | os.PathLike[str]) -> Section | None:
    """Where a file was downloaded from, per its NTFS Zone.Identifier stream.

    Windows (browsers, mail clients, archivers) attaches this alternate data
    stream to downloaded files. It is not part of the file's content, so it
    is not copied into a cleaned file, but it does reveal the download source
    while the file sits on disk. Returns None on other operating systems or
    when the stream doesn't exist.
    """
    if sys.platform != "win32":
        return None
    stream = f"{os.fspath(path)}:{ZONE_IDENTIFIER_STREAM}"
    section = Section("mark_of_the_web", "Download origin (Mark of the Web)")
    try:
        with open(stream, "rb") as f:
            raw = f.read(ZONE_IDENTIFIER_MAX_SIZE + 1)
    except PermissionError:
        section.add("status", "Zone.Identifier", "present but could not be read",
                    sensitive=True, note="permission denied")
        return section
    except OSError:
        # No such stream, or a file system without alternate data streams.
        return None

    if len(raw) > ZONE_IDENTIFIER_MAX_SIZE:
        section.add("status", "Zone.Identifier", "present but not read", sensitive=True,
                    note=f"stream is larger than {ZONE_IDENTIFIER_MAX_SIZE} bytes")
        return section

    entries = parse_zone_identifier(raw)
    if not entries:
        section.add("status", "Zone.Identifier", "present (no entries)", sensitive=True)
        return section
    for name, value in entries:
        key, label = _ZONE_LABELS.get(name.lower(), (name.lower(), name))
        if key == "zone_id" and value in _ZONE_NAMES:
            value = f"{value} ({_ZONE_NAMES[value]})"
        section.add(key, label, value, sensitive=True)
    return section


def parse_zone_identifier(raw: bytes) -> list[tuple[str, str]]:
    """Parse the INI-style Zone.Identifier text into (name, value) pairs.

    Section headers and blank lines are skipped; at most
    ZONE_IDENTIFIER_MAX_ENTRIES entries are returned.
    """
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("[", ";", "#")) or "=" not in line:
            continue
        name, value = line.split("=", 1)
        entries.append((name.strip(), value.strip()))
        if len(entries) >= ZONE_IDENTIFIER_MAX_ENTRIES:
            break
    return entries


# -- Hashes ----------------------------------------------------------------------

HASH_CHUNK_SIZE = 1024 * 1024


def hash_file(path: str | os.PathLike[str]) -> dict[str, str]:
    """MD5, SHA-1 and SHA-256 of a file, read from disk in 1 MiB chunks.

    MD5 and SHA-1 are included because many tools and databases still list
    them; they identify files, they are not used for any security decision.
    """
    name = os.fspath(path)
    digests = {
        "md5": hashlib.md5(usedforsecurity=False),
        "sha1": hashlib.sha1(usedforsecurity=False),
        "sha256": hashlib.sha256(),
    }
    try:
        with open(name, "rb") as f:
            while chunk := f.read(HASH_CHUNK_SIZE):
                for digest in digests.values():
                    digest.update(chunk)
    except OSError as exc:
        raise FileAccessError(f"{name}: cannot read file for hashing ({exc.strerror or exc})") from None
    return {algo: digest.hexdigest() for algo, digest in digests.items()}


def hashes_section(path: str | os.PathLike[str]) -> Section:
    hashes = hash_file(path)
    section = Section("hashes", "Hashes")
    section.add("md5", "MD5", hashes["md5"])
    section.add("sha1", "SHA-1", hashes["sha1"])
    section.add("sha256", "SHA-256", hashes["sha256"])
    return section
