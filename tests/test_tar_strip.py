"""Stripping TAR archives (formats/tar.py strip, core/tarwriter.py)."""

from __future__ import annotations

import pytest

from helpers.appledouble import sample_appledouble
from helpers.tar import (GNU, JAN_2024, V7, Header, build_tar, file, folder, gnu_long, hardlink,
                         pax, symlink)
from metaex.core import tar as tarcore
from metaex.core import tarwriter
from metaex.core.archive import NEUTRAL_TIME
from metaex.core.errors import CannotCleanError, MalformedFileError, UnsupportedFormatError
from metaex.formats import tar as tarformat

LONG = "deep/" + "x" * 120 + "/file.txt"
LONGER = "d/" + "y" * 300


def kitchen_sink() -> bytes:
    return build_tar([
        pax({"comment": "nightly backup", "uname": "jane.doe"}, flag=b"g"),
        Header("Backup 2024", b"V", format=GNU),
        folder("home/jane.doe", uname=b"jane.doe", gname=b"staff", uid=501, gid=20,
               mtime=JAN_2024, mode=0o700),
        pax({"mtime": "1714559400.25", "SCHILY.xattr.com.apple.quarantine": "0083;1;Safari;X",
             "comment": "draft", "uname": "jane"}),
        file("home/jane.doe/plan.txt", b"plan", mode=0o600, mtime=JAN_2024,
             padding=b"hidden" + bytes(502)),
        file("home/jane.doe/._plan.txt", sample_appledouble()),
        file("home/jane.doe/.DS_Store", b"\x00\x00\x00\x01Bud1"),
        file("run.sh", b"#!/bin/sh\n", mode=0o775, format=GNU, atime=JAN_2024),
        file("old.txt", b"first version with a secret"),
        file("old.txt", b"second version"),
        gnu_long(LONG),
        file("deep/x", b"long", format=GNU),
        pax({"path": LONGER, "linkpath": "t/" + "z" * 200}),
        symlink("ignored", "ignored"),
        symlink("rel", "home/jane.doe/plan.txt"),
        hardlink("hard", "run.sh"),
        Header("dev/null", b"3", devmajor=1, devminor=3),
        Header("fifo", b"6", mode=0o600),
        file("v7.txt", b"v7", format=V7, mtime=JAN_2024),
    ], suffix=b"after the end")


def test_strip_keeps_entries_and_removes_metadata():
    original = kitchen_sink()
    cleaned = tarformat.strip(original)
    archive = tarcore.open_tar(cleaned)
    assert [e.name for e in archive.entries] == [
        "home/jane.doe/", "home/jane.doe/plan.txt", "run.sh", "old.txt", LONG, LONGER, "rel",
        "hard", "dev/null", "fifo", "v7.txt"]
    for e in archive.entries:
        h = e.header
        assert h.format == "ustar"
        assert (h.mtime, h.uid, h.gid, h.uname, h.gname) == (NEUTRAL_TIME, 0, 0, b"", b"")
        assert set(e.pax) <= {"path", "linkpath"}
    modes = {e.name: e.header.mode for e in archive.entries}
    assert modes["home/jane.doe/"] == 0o755 and modes["home/jane.doe/plan.txt"] == 0o644
    assert modes["run.sh"] == 0o755 and modes["rel"] == 0o777 and modes["fifo"] == 0o644
    data = {e.name: archive.read(e) for e in archive.entries}
    assert data["old.txt"] == b"second version" and data["home/jane.doe/plan.txt"] == b"plan"
    links = {e.name: e.linkname for e in archive.entries}
    assert links[LONGER] == "t/" + "z" * 200 and links["hard"] == "run.sh"
    assert archive.entries[8].header.devminor == 3
    assert archive.globals_ == [] and archive.labels == [] and archive.hidden == []
    assert archive.end_blocks == 2 and len(cleaned) % 10240 == 0
    for secret in (b"hidden", b"after the end", b"first version", b"jane\x00", b"Safari",
                   b"draft", b"nightly", b"Backup 2024"):
        assert secret not in cleaned


def test_pax_only_for_names_that_need_it():
    cleaned = tarformat.strip(kitchen_sink())
    archive = tarcore.open_tar(cleaned)
    with_pax = [e.name for e in archive.entries if e.pax]
    assert with_pax == [LONGER]  # LONG fits the ustar prefix and name fields
    assert archive.entries[4].header.name == LONG.encode()


def test_report_of_the_cleaned_archive():
    found = tarformat.parse(tarformat.strip(kitchen_sink()))
    assert [s.key for s in found["sections"]] == ["archive_contents", "archive_paths",
                                                  "archive_links", "archive_files"]


def test_strip_is_deterministic_and_idempotent():
    once = tarformat.strip(kitchen_sink())
    assert tarformat.strip(kitchen_sink()) == once
    assert tarformat.strip(once) == once


def test_names_keep_their_bytes():
    data = build_tar([file(b"caf\xe9.txt", b"latin-1 name"), file("naïve.txt", b"utf-8")])
    archive = tarcore.open_tar(tarformat.strip(data))
    assert [e.raw_name for e in archive.entries] == [b"caf\xe9.txt", "naïve.txt".encode()]


def test_removed_sizes_and_warnings():
    data = kitchen_sink()
    sizes = tarformat.removed_sizes(data)
    # the padding after plan.txt (508 bytes), the data after the end, the replaced copy
    assert sizes == {"archive_hidden": 508 + 13 + len(b"first version with a secret")}
    warning = tarformat.strip_warnings(data)[0]
    assert "removed 2 operating system file(s)" in warning
    assert tarformat.kept_warnings(tarformat.strip(data))[0].startswith("CRITICAL")


@pytest.mark.parametrize("target", ["/etc/passwd", "../../x", "/home/jane.doe/x"])
def test_unsafe_symlinks_are_refused(target):
    with pytest.raises(CannotCleanError):
        tarformat.strip(build_tar([symlink("dir/l", target)]))


def test_hard_link_to_a_missing_or_removed_file_is_refused():
    with pytest.raises(CannotCleanError, match="hard link"):
        tarformat.strip(build_tar([hardlink("h", "missing")]))
    with pytest.raises(CannotCleanError, match="hard link"):
        tarformat.strip(build_tar([file(".DS_Store", b"x"), hardlink("h", ".DS_Store")]))


def test_unsupported_and_damaged_archives_are_refused():
    with pytest.raises(UnsupportedFormatError):
        tarformat.strip(build_tar([file("a", b"x", typeflag=b"S")]))
    with pytest.raises(MalformedFileError):
        tarformat.strip(build_tar([file("a", b"x", checksum=1)]))
    with pytest.raises(MalformedFileError):
        tarformat.strip(build_tar([file("../a", b"x")]))


def test_empty_archive():
    cleaned = tarformat.strip(build_tar([pax({"comment": "x"}, flag=b"g")]))
    assert cleaned == bytes(10240)


# -- the writer ---------------------------------------------------------------------------


def test_split_name():
    assert tarwriter.split_name(b"a" * 100) == (b"", b"a" * 100)
    assert tarwriter.split_name(b"p" * 155 + b"/" + b"n" * 100) == (b"p" * 155, b"n" * 100)
    assert tarwriter.split_name(b"p" * 156 + b"/" + b"n" * 10) is None
    assert tarwriter.split_name(b"x" * 101) is None


@pytest.mark.parametrize("value", [b"", b"a" * 90, b"a" * 93, b"a" * 994, b"a" * 995])
def test_pax_record_lengths(value):
    record = tarwriter.pax_record("path", value)
    length, _, _ = record.partition(b" ")
    assert int(length) == len(record)


def test_header_checksum_is_valid():
    block = tarwriter.header(b"a.txt", b"", b"0", 0o644, 5)
    assert tarcore.checksum_ok(block) and len(block) == 512
