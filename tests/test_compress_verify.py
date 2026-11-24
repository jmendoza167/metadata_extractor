"""Verifying cleaned gzip, bzip2 and xz files (core/compress.py verify_details)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from helpers import bzip2, gz, xz
from helpers.jpeg import sample_photo
from helpers.tar import JAN_2024, build_tar, file, pax
from metaex.core.archive import scan_inner
from metaex.core.verify import verify
from metaex.formats import gzip as gzipformat

TAR = build_tar([pax({"comment": "nightly"}, flag=b"g"),
                 file("docs/a.txt", b"hello", uname=b"jane.doe", mtime=JAN_2024)])
WRAPS = [lambda d: gz.gzip(d, name=b"x", mtime=JAN_2024, comment=b"c"), bzip2.compress, xz.xz]


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


@pytest.mark.parametrize("wrap", WRAPS)
def test_cleaned_compressed_tar_verifies(wrap):
    from metaex.core import compress

    original = wrap(TAR)
    cleaned = compress.strip(original, compress.kind_of(original))
    result = verify(cleaned, original)
    assert result.clean, (result.leftovers, result.problems)
    assert result.image_data_match is True


@pytest.mark.parametrize("wrap", WRAPS)
def test_original_is_not_clean(wrap):
    assert not verify(wrap(TAR)).clean


def test_gzip_header_is_a_leftover():
    result = verify(gz.gzip(b"plain file", mtime=1))
    assert not result.clean
    assert any("member 1 header" in str(f) for f in result.leftovers)


def test_neutral_gzip_with_padding_or_trailing_data_is_not_clean():
    clean = gz.gzip(b"plain file")
    assert verify(clean).clean
    assert not verify(clean + bytes(8)).clean
    assert not verify(clean + b"appended").clean


def test_single_file_must_be_unchanged():
    photo = sample_photo()
    original = gz.gzip(photo, name=b"photo.jpg")
    result = verify(gzipformat.strip(original), original)
    assert result.image_data_match  # the same photo ...
    assert not result.clean  # ... with its GPS: without --deep the file inside is not cleaned
    with scan_inner(False):  # the wrapper itself is clean
        result = verify(gzipformat.strip(original), original)
    assert result.clean and result.image_data_match
    assert result.image_label == "Compressed file contents"
    other = gz.gzip(photo[:-1] + b"\x00")
    assert verify(other, original).image_data_match is False


def test_tar_inside_must_be_clean():
    result = verify(gz.gzip(TAR))
    assert not result.clean
    assert any("Entry owners" in str(f) for f in result.leftovers)


def test_cli_strip_and_verify(tmp_path):
    path = tmp_path / "backup.tar.gz"
    path.write_bytes(gz.gzip(TAR, name=b"backup.tar", mtime=JAN_2024) + b"appended")
    r = metaex("strip", path)
    assert r.returncode == 0, r.stderr
    assert "Verification: clean" in r.stdout
    cleaned = tmp_path / "backup_clean.tar.gz"
    assert cleaned.exists()
    r = metaex("verify", cleaned, "--original", path)
    assert r.returncode == 0, r.stdout
    assert "Archive contents: matches the original" in r.stdout


def test_cli_strip_plain_xz(tmp_path):
    path = tmp_path / "notes.txt.xz"
    path.write_bytes(xz.xz(b"hello", streams=2, padding=4))
    r = metaex("strip", path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "notes_clean.txt.xz").exists()


def test_cli_refuses_xz_with_trailing_garbage(tmp_path):
    path = tmp_path / "broken.xz"
    path.write_bytes(xz.xz(b"hello") + b"garbage!")
    r = metaex("strip", path)
    assert r.returncode == 1 and "no file written" in r.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["broken.xz"]
