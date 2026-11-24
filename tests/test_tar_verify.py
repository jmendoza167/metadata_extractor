"""Verifying cleaned TAR archives (formats/tar.py verify_details)."""

from __future__ import annotations

import subprocess
import sys

from helpers.appledouble import sample_appledouble
from helpers.tar import GNU, JAN_2024, build_tar, file, folder, pax, symlink
from metaex.core.verify import verify
from metaex.formats import tar as tarformat


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


def original() -> bytes:
    return build_tar([
        pax({"comment": "nightly"}, flag=b"g"),
        folder("docs", uname=b"jane.doe", mtime=JAN_2024, mode=0o700),
        pax({"atime": "1714559401", "SCHILY.xattr.user.tag": "red"}),
        file("docs/a.txt", b"hello", uid=1000, gid=1000),
        file("docs/._a.txt", sample_appledouble()),
        file("run.sh", b"#!/bin/sh\n", mode=0o750, format=GNU, atime=JAN_2024),
        symlink("link", "docs/a.txt"),
        file("docs/a.txt", b"hello again"),
    ], suffix=b"hidden")


def test_cleaned_archive_verifies():
    data = original()
    result = verify(tarformat.strip(data), data)
    assert result.clean, (result.leftovers, result.problems)
    assert result.image_data_match is True
    assert {f.section for f in result.kept_by_design} == {"Archive contents", "Link targets"}


def test_original_is_not_clean():
    result = verify(original())
    assert not result.clean
    text = " ".join(str(f) for f in result.leftovers)
    for expected in ("Hidden data", "Entry times", "Entry owners", "Operating system files",
                     "Extended attributes", "global pax headers", "gnu header"):
        assert expected in text, expected


def test_tarfile_output_is_not_clean():
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as t:
        info = tarfile.TarInfo("a.txt")
        info.size = 1
        t.addfile(info, io.BytesIO(b"a"))  # mtime 0: not metaex's neutral time
    result = verify(buf.getvalue())
    assert not result.clean


def test_different_contents_do_not_match():
    data = original()
    other = tarformat.strip(build_tar([folder("docs"), file("docs/a.txt", b"HELLO AGAIN"),
                                       file("run.sh", b"#!/bin/sh\n", mode=0o755),
                                       symlink("link", "docs/a.txt")]))
    result = verify(other, data)
    assert result.image_data_match is False


def test_missing_entry_is_a_problem():
    data = build_tar([file("a", b"x"), file("b", b"y")])
    result = verify(tarformat.strip(build_tar([file("a", b"x")])), data)
    assert not result.clean and "missing b" in result.problems[0]


def test_missing_end_blocks_are_a_leftover():
    cleaned = tarformat.strip(build_tar([file("a", b"x")]))
    result = verify(cleaned[:1024])
    assert not result.clean


def test_cli_strip_then_verify(tmp_path):
    path = tmp_path / "backup.tar"
    path.write_bytes(original())
    r = metaex("strip", path)
    assert r.returncode == 0, r.stderr
    assert "Verification: clean" in r.stdout
    assert "removed 1 operating system file(s): docs/._a.txt" in r.stdout
    r = metaex("verify", tmp_path / "backup_clean.tar", "--original", path)
    assert r.returncode == 0, r.stdout
    r = metaex("verify", path)
    assert r.returncode == 3
