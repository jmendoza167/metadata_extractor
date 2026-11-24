"""Cleaned TAR archives read by an independent reader (the stdlib tarfile
module), and damaged or mutated archives that must be refused cleanly."""

from __future__ import annotations

import io
import random
import tarfile

import pytest

from helpers.appledouble import sample_appledouble
from helpers.tar import (GNU, JAN_2024, V7, Header, build_tar, file, folder, gnu_long, hardlink,
                         pax, symlink)
from metaex.core.errors import MetaexError
from metaex.core.verify import verify
from metaex.formats import tar as tarformat


def stdlib_archive(fmt: int) -> bytes:
    """An archive written by tarfile, with owners, times and pax records."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as t:
        for name, data, mode, kind in (("docs", b"", 0o700, tarfile.DIRTYPE),
                                       ("docs/a.txt", b"alpha", 0o600, tarfile.REGTYPE),
                                       ("docs/" + "deep/" * 24 + "a.txt", b"long", 0o644,
                                        tarfile.REGTYPE),
                                       ("bin/run", b"#!/bin/sh\n", 0o750, tarfile.REGTYPE)):
            info = tarfile.TarInfo(name)
            info.type, info.mode, info.size = kind, mode, len(data)
            info.mtime, info.uid, info.gid = JAN_2024, 1000, 1000
            info.uname, info.gname = "jane.doe", "staff"
            if fmt == tarfile.PAX_FORMAT:
                info.pax_headers = {"comment": "made with tarfile", "atime": "1714559401"}
            t.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("docs/link")
        link.type, link.linkname = tarfile.SYMTYPE, "a.txt"
        t.addfile(link)
    return buf.getvalue()


def kitchen_sink() -> bytes:
    return build_tar([
        pax({"comment": "nightly"}, flag=b"g"),
        folder("home/jane.doe", uname=b"jane.doe", mtime=JAN_2024),
        file("home/jane.doe/a.txt", b"hello", padding=b"x" * 507),
        file("home/jane.doe/._a.txt", sample_appledouble()),
        gnu_long("home/jane.doe/" + "n" * 150),
        file("ignored", b"gnu long", format=GNU, atime=JAN_2024),
        file("v7.txt", b"v7", format=V7),
        symlink("lnk", "v7.txt"),
        hardlink("hard", "v7.txt"),
        Header("dev/zero", b"3", devmajor=1, devminor=5),
    ], suffix=b"tail")


@pytest.mark.parametrize("make", [lambda: stdlib_archive(tarfile.USTAR_FORMAT),
                                  lambda: stdlib_archive(tarfile.GNU_FORMAT),
                                  lambda: stdlib_archive(tarfile.PAX_FORMAT), kitchen_sink])
def test_tarfile_reads_the_cleaned_archive(make):
    original = make()
    cleaned = tarformat.strip(original)
    with tarfile.open(fileobj=io.BytesIO(original)) as t:
        before = {m.name: (m, t.extractfile(m).read() if m.isfile() else b"")
                  for m in t.getmembers()}
    with tarfile.open(fileobj=io.BytesIO(cleaned)) as t:
        members = t.getmembers()
        for m in members:
            old, data = before[m.name]
            assert (m.mtime, m.uid, m.gid, m.uname, m.gname) == (315532800, 0, 0, "", "")
            assert m.type == old.type or (old.type == tarfile.AREGTYPE and m.isfile())
            if m.isfile():
                assert t.extractfile(m).read() == data
                assert m.mode == (0o755 if old.mode & 0o111 else 0o644)
            elif m.isdir():
                assert m.mode == 0o755
            if m.issym() or m.islnk():
                assert m.linkname == old.linkname
            assert set(m.pax_headers) <= {"path", "linkpath"}
    junk = {n for n in before if n.rsplit("/", 1)[-1].startswith("._")}
    assert [m.name for m in members] == [n for n in before if n not in junk]


def test_cleaned_archive_holds_no_secret_bytes():
    cleaned = tarformat.strip(kitchen_sink())
    for secret in (b"jane.doe\x00", b"nightly", b"xxxx", b"tail", b"Safari"):
        assert secret not in cleaned


def test_tarfile_extracts_nothing_outside_with_the_data_filter(tmp_path):
    cleaned = tarformat.strip(stdlib_archive(tarfile.PAX_FORMAT))
    with tarfile.open(fileobj=io.BytesIO(cleaned)) as t:
        if hasattr(tarfile, "data_filter"):
            t.extractall(tmp_path, filter="data")
        else:  # Python 3.10 before 3.10.12
            t.extractall(tmp_path)
    assert (tmp_path / "docs" / "a.txt").read_bytes() == b"alpha"


def test_strip_and_verify():
    for original in (kitchen_sink(), stdlib_archive(tarfile.PAX_FORMAT)):
        result = verify(tarformat.strip(original), original)
        assert result.clean and result.image_data_match


# -- damaged and mutated archives ---------------------------------------------------------


@pytest.mark.parametrize("cut", [100, 511, 600, 1100, 1600])
def test_truncated_archives_are_refused(cut):
    data = build_tar([file("a", b"x" * 1000), file("b", b"y" * 100)], record=False,
                     end_blocks=0)
    with pytest.raises(MetaexError):
        tarformat.strip(data[:cut])


def test_mutations_never_crash_and_never_pass_unchecked():
    """Change random header bytes: strip either refuses with a metaex error,
    or its output verifies clean against the mutated input."""
    original = build_tar([pax({"mtime": "1"}), file("a.txt", b"alpha" * 30),
                          folder("d"), symlink("d/l", "../a.txt"), file("d/b", b"beta")])
    headers = [i for i in range(0, len(original), 512) if original[i:i + 512].strip(b"\x00")]
    rng = random.Random(11)
    refused = 0
    for _ in range(300):
        data = bytearray(original)
        start = rng.choice(headers)
        for _ in range(rng.randint(1, 3)):
            data[start + rng.randrange(512)] = rng.randrange(256)
        data = bytes(data)
        try:
            cleaned = tarformat.strip(data)
        except MetaexError:
            refused += 1
            continue
        result = verify(cleaned, data)
        assert result.clean and result.image_data_match, (result.leftovers, result.problems)
    assert refused > 100  # most changes break a checksum


def test_mutated_reports_never_crash():
    original = build_tar([pax({"atime": "1", "SCHILY.xattr.a": "b"}), file("a.txt", b"x"),
                          file("._a.txt", sample_appledouble())])
    rng = random.Random(12)
    for _ in range(300):
        data = bytearray(original)
        data[rng.randrange(len(original))] = rng.randrange(256)
        try:
            tarformat.parse(bytes(data))
        except MetaexError:
            pass
