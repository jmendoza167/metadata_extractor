"""Cleaned ZIP archives read by an independent reader (the stdlib zipfile
module), and damaged or mutated archives that must be refused cleanly."""

from __future__ import annotations

import io
import random
import stat
import zipfile

import pytest

from helpers.appledouble import sample_appledouble
from helpers.zip import (DEFLATED, Entry, build_zip, dos_stamp, ntfs_times, unix_ids,
                         unix_times)
from metaex.core.errors import MetaexError
from metaex.core.verify import verify
from metaex.formats import zip as zipformat

UNIX = 3 << 8 | 20
DATE, TIME = dos_stamp(2024, 5, 1, 10, 30)


def stdlib_archive() -> bytes:
    """An archive written by zipfile with times, permissions, comments."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.comment = b"made with zipfile"
        for name, data, mode in (("docs/", b"", 0o40700), ("docs/a.txt", b"alpha " * 200,
                                                            0o100600),
                                 ("bin/run", b"#!/bin/sh\n", 0o100750),
                                 ("docs/.DS_Store", b"\x00\x00\x00\x01Bud1", 0o100644)):
            info = zipfile.ZipInfo(name, (2024, 5, 1, 10, 30, 0))
            info.create_system = 3
            info.external_attr = mode << 16 | (0x10 if name.endswith("/") else 0)
            info.comment = b"entry comment"
            info.compress_type = zipfile.ZIP_DEFLATED if data else zipfile.ZIP_STORED
            z.writestr(info, data)
    return buf.getvalue()


def kitchen_sink() -> bytes:
    return build_zip([
        Entry("plans/floor.txt", b"Kitchen and dining " * 50, method=DEFLATED, date=DATE,
              time=TIME, local_extra=unix_times(1714559400) + unix_ids(1000, 1000)
              + ntfs_times(1, 2, 3), comment=b"draft"),
        Entry("plans/run.sh", b"#!/bin/sh\n", created_by=UNIX, external_attr=0o100775 << 16,
              descriptor=True),
        Entry("__MACOSX/plans/._floor.txt", sample_appledouble()),
        Entry("Thumbs.db", b"\xd0\xcf\x11\xe0"),
        Entry("café/naïve.txt", b"unicode name"),
    ], comment=b"Backup", prefix=b"PREFIX", suffix=b"SUFFIX")


@pytest.mark.parametrize("make", [stdlib_archive, kitchen_sink])
def test_zipfile_reads_the_cleaned_archive(make):
    original = make()
    cleaned = zipformat.strip(original)
    with zipfile.ZipFile(io.BytesIO(original)) as z:
        before = {i.filename: (z.read(i), i) for i in z.infolist()}
    with zipfile.ZipFile(io.BytesIO(cleaned)) as z:
        assert z.testzip() is None
        assert z.comment == b""
        infos = z.infolist()
        for info in infos:
            data, old = before[info.filename]
            assert z.read(info) == data
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.extra == b"" and info.comment == b""
            assert info.create_system == 3
            mode = info.external_attr >> 16
            if info.is_dir():
                assert mode == 0o40755 and info.external_attr & 0x10
            elif (old.external_attr >> 16) & 0o111 and old.create_system == 3:
                assert mode == 0o100755
            else:
                assert mode == 0o100644
            assert stat.S_IMODE(mode) in (0o644, 0o755)
    junk = {n for n in before if n.endswith((".DS_Store", "Thumbs.db")) or "__MACOSX" in n}
    assert [i.filename for i in infos] == [n for n in before if n not in junk]


def test_cleaned_archive_has_nothing_outside_the_entries():
    cleaned = zipformat.strip(kitchen_sink())
    assert cleaned.startswith(b"PK\x03\x04")
    assert cleaned.endswith(b"\x00\x00")  # the end record with an empty comment
    assert b"PREFIX" not in cleaned and b"SUFFIX" not in cleaned and b"Backup" not in cleaned
    assert b"draft" not in cleaned and b"Safari" not in cleaned


def test_unicode_names_are_flagged_utf8():
    cleaned = zipformat.strip(kitchen_sink())
    with zipfile.ZipFile(io.BytesIO(cleaned)) as z:
        info = z.getinfo("café/naïve.txt")
        assert info.flag_bits & 0x800


def test_strip_and_verify_on_the_cleaned_archive():
    original = kitchen_sink()
    result = verify(zipformat.strip(original), original)
    assert result.clean and result.image_data_match


# -- damaged and mutated archives ---------------------------------------------------------


@pytest.mark.parametrize("cut", [10, 60, 150, 300, -30, -10])  # -1 only shortens the suffix
def test_truncated_archives_are_refused(cut):
    data = kitchen_sink()
    with pytest.raises(MetaexError):
        zipformat.strip(data[:cut])


def test_mutations_never_crash_and_never_pass_unchecked():
    """Flip random bytes: strip either refuses with a metaex error, or its
    output verifies clean against the mutated input (the mutation hit data
    that stays valid, e.g. a stored entry's contents)."""
    original = build_zip([Entry("a.txt", b"alpha " * 30), Entry("b/c.txt", b"beta" * 40,
                                                                method=DEFLATED),
                          Entry("._x", sample_appledouble())], comment=b"note")
    rng = random.Random(8)
    for _ in range(300):
        data = bytearray(original)
        for _ in range(rng.randint(1, 4)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        data = bytes(data)
        try:
            cleaned = zipformat.strip(data)
        except MetaexError:
            continue
        result = verify(cleaned, data)
        assert result.clean and result.image_data_match, result.problems


def test_mutated_reports_never_crash():
    original = build_zip([Entry("a.txt", b"x", local_extra=unix_times(1) + ntfs_times(1, 2, 3)),
                          Entry("._x", sample_appledouble())])
    rng = random.Random(9)
    for _ in range(300):
        data = bytearray(original)
        data[rng.randrange(len(data))] = rng.randrange(256)
        try:
            zipformat.parse(bytes(data))
        except MetaexError:
            pass
