"""Stripping plain ZIP archives (formats/zip.py strip)."""

from __future__ import annotations

import struct
import zlib

import pytest

from helpers.appledouble import sample_appledouble
from helpers.zip import (DEFLATED, FLAG_ENCRYPTED, Entry, build_zip, dos_stamp, extra,
                         ntfs_times, unix_ids, unix_times)
from metaex.core.errors import CannotCleanError, MalformedFileError
from metaex.core.ziparchive import NO_TIME, open_archive
from metaex.formats import zip as zipformat

UNIX = 3 << 8 | 20
DATE, TIME = dos_stamp(2024, 5, 1, 10, 30)


def kitchen_sink() -> bytes:
    return build_zip([
        Entry("plans/", date=DATE, time=TIME, created_by=UNIX,
              external_attr=(0o40700 << 16) | 0x10),
        Entry("plans/floor.txt", b"Kitchen and dining " * 20, method=DEFLATED, date=DATE,
              time=TIME, local_extra=unix_times(1714559400) + unix_ids(1000, 1000)
              + ntfs_times(1, 2, 3), central_extra=unix_times(1714559400),
              comment=b"draft", created_by=UNIX, external_attr=0o100600 << 16),
        Entry("plans/run.sh", b"#!/bin/sh\necho hi\n", created_by=UNIX,
              external_attr=0o100775 << 16, descriptor=True),
        Entry("plans/link", b"floor.txt", created_by=UNIX, external_attr=0o120755 << 16),
        Entry("__MACOSX/"), Entry("__MACOSX/plans/"),
        Entry("__MACOSX/plans/._floor.txt", sample_appledouble()),
        Entry("plans/._run.sh", sample_appledouble()),
        Entry("plans/.DS_Store", b"\x00\x00\x00\x01Bud1"),
        Entry("Thumbs.db", b"\xd0\xcf\x11\xe0"),
        Entry("desktop.ini", b"[.ShellClassInfo]"),
        Entry("notes.txt", b"plain", created_by=10 << 8 | 20, external_attr=0x22),
    ], comment=b"Backup of Jane's laptop", prefix=b"PREFIX", between={1: b"GAP"},
        suffix=b"SUFFIX")


KEPT = ["plans/", "plans/floor.txt", "plans/run.sh", "plans/link", "notes.txt"]


def test_strip_keeps_files_and_removes_metadata():
    original = kitchen_sink()
    cleaned = zipformat.strip(original)
    archive = open_archive(cleaned)
    assert archive.names() == KEPT
    assert archive.comment == b"" and archive.hidden == []
    source = open_archive(original)
    for e in archive.entries:
        assert (e.dos_date, e.dos_time) == NO_TIME
        assert e.extra == [] and e.comment == b""
        assert e.created_by == UNIX and e.flags == 0
        assert archive.read(e) == source.read(e.name)
        assert archive.raw(e) == source.raw(source.get(e.name))  # not compressed again
    modes = {e.name: e.external_attr >> 16 for e in archive.entries}
    assert modes == {"plans/": 0o40755, "plans/floor.txt": 0o100644,
                     "plans/run.sh": 0o100755, "plans/link": 0o120777,
                     "notes.txt": 0o100644}
    assert archive.get("plans/").external_attr & 0x10


def test_report_of_the_cleaned_archive_is_empty():
    cleaned = zipformat.strip(kitchen_sink())
    keys = [s.key for s in zipformat.parse(cleaned)["sections"]]
    assert keys == ["archive_contents", "archive_links", "archive_files"]


def test_strip_is_deterministic():
    assert zipformat.strip(kitchen_sink()) == zipformat.strip(kitchen_sink())


def test_already_clean_archive_is_unchanged_by_a_second_strip():
    once = zipformat.strip(kitchen_sink())
    assert zipformat.strip(once) == once


def test_unicode_path_extra_field_gives_the_name():
    raw = b"cafe.txt"
    field = extra(0x7075, b"\x01" + struct.pack("<I", zlib.crc32(raw)) + "café.txt".encode())
    data = build_zip([Entry(raw, b"x", flags=0, local_extra=field)])
    assert open_archive(zipformat.strip(data)).names() == ["café.txt"]


def test_unicode_path_with_wrong_crc_is_ignored():
    field = extra(0x7075, b"\x01" + struct.pack("<I", 1234) + b"other.txt")
    data = build_zip([Entry("a.txt", b"x", local_extra=field)])
    assert open_archive(zipformat.strip(data)).names() == ["a.txt"]


def test_unicode_path_that_goes_up_a_folder_is_refused():
    field = extra(0x7075, b"\x01" + struct.pack("<I", zlib.crc32(b"a.txt")) + b"../a.txt")
    with pytest.raises(MalformedFileError, match="path traversal"):
        zipformat.strip(build_zip([Entry("a.txt", b"x", local_extra=field)]))


def test_removed_sizes_and_warnings():
    data = kitchen_sink()
    assert zipformat.removed_sizes(data) == {"archive_hidden": 15}
    warning = zipformat.strip_warnings(data)[0]
    assert warning.startswith("removed 7 operating system file(s): __MACOSX, ")
    for name in ("plans/.DS_Store", "Thumbs.db", "desktop.ini", "plans/._run.sh"):
        assert name in warning


def test_user_folder_names_are_kept_with_a_warning():
    data = build_zip([Entry("home/jane.doe/notes.txt", b"x")])
    cleaned = zipformat.strip(data)
    assert open_archive(cleaned).names() == ["home/jane.doe/notes.txt"]
    warning = zipformat.kept_warnings(cleaned)[0]
    assert warning.startswith("CRITICAL") and "'jane.doe'" in warning


@pytest.mark.parametrize("target", ["/etc/passwd", "../../outside", "/home/jane.doe/x",
                                    "C:/Users/jane.doe/x"])
def test_unsafe_links_are_refused(target):
    data = build_zip([Entry("dir/link", target.encode(), created_by=UNIX,
                            external_attr=0o120777 << 16)])
    with pytest.raises(CannotCleanError):
        zipformat.strip(data)


def test_signed_archives_are_refused():
    data = build_zip([Entry("META-INF/MANIFEST.MF", b"x"), Entry("META-INF/A.RSA", b"x")])
    with pytest.raises(CannotCleanError, match="signed"):
        zipformat.strip(data)
    block = struct.pack("<Q", 24) * 2 + b"APK Sig Block 42"
    with pytest.raises(CannotCleanError, match="APK signing block"):
        zipformat.strip(build_zip([Entry("a", b"x")], before_directory=block))


def test_encrypted_entries_are_refused():
    with pytest.raises(MalformedFileError, match="encrypted"):
        zipformat.strip(build_zip([Entry("a.txt", b"x", flags=FLAG_ENCRYPTED)]))


def test_fifo_entries_are_refused():
    data = build_zip([Entry("pipe", b"", created_by=UNIX, external_attr=0o10644 << 16)])
    with pytest.raises(CannotCleanError, match="fifo"):
        zipformat.strip(data)


@pytest.mark.parametrize("data", [
    b"", build_zip([Entry("../evil", b"x")]),
    build_zip([Entry("a.txt", b"hello", crc=1)]),
    build_zip([Entry("a.txt", b"hello", method=DEFLATED, compressed=b"\xff\xff")]),
])
def test_damaged_archives_are_refused(data):
    with pytest.raises(MalformedFileError):
        zipformat.strip(data)


def test_empty_archive():
    cleaned = zipformat.strip(build_zip([], comment=b"empty"))
    assert open_archive(cleaned).names() == [] and open_archive(cleaned).comment == b""
