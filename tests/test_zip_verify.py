"""Verifying cleaned ZIP archives (formats/zip.py verify_details)."""

from __future__ import annotations

import subprocess
import sys

from helpers.appledouble import sample_appledouble
from helpers.zip import Entry, build_zip, dos_stamp, make_zip, unix_ids, unix_times
from metaex.core.verify import verify
from metaex.formats import zip as zipformat

UNIX = 3 << 8 | 20
DATE, TIME = dos_stamp(2024, 5, 1, 10, 30)


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


def original() -> bytes:
    return build_zip([
        Entry("a.txt", b"hello", date=DATE, time=TIME, local_extra=unix_times(1714559400)
              + unix_ids(), comment=b"note"),
        Entry("run.sh", b"#!/bin/sh\n", created_by=UNIX, external_attr=0o100750 << 16),
        Entry("__MACOSX/._a.txt", sample_appledouble()),
        Entry("link", b"a.txt", created_by=UNIX, external_attr=0o120777 << 16),
    ], comment=b"archive comment", suffix=b"hidden")


def test_cleaned_archive_verifies():
    data = original()
    result = verify(zipformat.strip(data), data)
    assert result.clean, (result.leftovers, result.problems)
    assert result.image_data_match is True
    assert result.image_label == "Archive contents"
    kept = {f.section for f in result.kept_by_design}
    assert kept == {"Archive contents", "Link targets", "Files inside"}


def test_original_is_not_clean():
    data = original()
    result = verify(data)
    assert not result.clean
    leftovers = " ".join(str(f) for f in result.leftovers)
    for text in ("Hidden data", "Archive comments", "Entry times", "Operating system files",
                 "Extended attributes", "made by MS-DOS"):
        assert text in leftovers, text


def test_neutral_looking_headers_are_still_checked():
    # made by MS-DOS, no times, no extra fields: the report shows nothing,
    # but a metaex-cleaned archive is always "made by" Unix with fixed modes
    result = verify(make_zip({"a.txt": "x"}))
    assert not result.clean
    assert any("made by MS-DOS" in str(f) for f in result.leftovers)


def test_wrong_mode_is_a_leftover():
    cleaned = bytearray(zipformat.strip(make_zip({"a.txt": "x"})))
    cd = cleaned.index(b"PK\x01\x02")
    cleaned[cd + 38:cd + 42] = (0o100600 << 16).to_bytes(4, "little")
    result = verify(bytes(cleaned))
    assert not result.clean


def test_changed_contents_do_not_match():
    data = original()
    other = zipformat.strip(build_zip([Entry("a.txt", b"HELLO"), Entry("run.sh", b"#!/bin/sh\n"),
                                       Entry("link", b"a.txt", created_by=UNIX,
                                             external_attr=0o120777 << 16)]))
    result = verify(other, data)
    assert result.image_data_match is False


def test_missing_entry_is_a_problem():
    data = make_zip({"a.txt": "x", "b.txt": "y"})
    other = zipformat.strip(make_zip({"a.txt": "x"}))
    result = verify(other, data)
    assert not result.clean and "missing b.txt" in result.problems[0]


def test_unreadable_files_are_problems():
    result = verify(build_zip([Entry("a.txt", b"x")])[:-3] + b"PK\x05\x06" + bytes(18))
    assert not result.clean


def test_cli_strip_then_verify(tmp_path):
    path = tmp_path / "plans.zip"
    path.write_bytes(original())
    r = metaex("strip", path)
    assert r.returncode == 0, r.stderr
    assert "Verification: CLEAN" in r.stdout
    assert "removed 1 operating system file(s): __MACOSX/._a.txt" in r.stdout
    cleaned = tmp_path / "plans_clean.zip"
    r = metaex("verify", cleaned, "--original", path)
    assert r.returncode == 0, r.stdout
    assert "Archive contents: matches the original" in r.stdout
    r = metaex("verify", path)
    assert r.returncode == 3
