"""Stripping gzip, bzip2 and xz files (core/compress.py rewrap and strip)."""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import tarfile

import pytest

from helpers import bzip2, gz, xz
from helpers.jpeg import sample_photo
from helpers.tar import JAN_2024, build_tar, file, pax
from metaex.commands.strip import default_output_path
from metaex.core import compress
from metaex.core import filetype as ft
from metaex.core.errors import MalformedFileError
from metaex.formats import bzip2 as bzip2format
from metaex.formats import gzip as gzipformat
from metaex.formats import xz as xzformat

TAR = build_tar([pax({"comment": "nightly"}, flag=b"g"),
                 file("docs/a.txt", b"hello", uname=b"jane.doe", mtime=JAN_2024),
                 file("docs/.DS_Store", b"\x00\x00\x00\x01Bud1")])


def tar_names(data: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        return [m.name for m in t.getmembers()]


def test_gzip_single_file_keeps_its_compressed_data():
    photo = sample_photo()
    data = gz.member(photo, name=b"photo.jpg", mtime=JAN_2024, os=3, comment=b"c",
                     extra=gz.subfield(b"AB", b"x"), header_crc=True) + b"appended"
    cleaned = gzipformat.strip(data)
    assert cleaned[:10] == compress.NEUTRAL_GZIP_HEADER
    assert cleaned[10:] == gz.stored_deflate(photo) + data[-16:-8]  # deflate + trailer
    assert gzip.decompress(cleaned) == photo
    assert compress.open_compressed(cleaned).trailing is None


def test_gzip_members_stay_members():
    data = gz.member(b"one", name=b"a") + gz.member(b"two", mtime=5) + bytes(20)
    cleaned = gzipformat.strip(data)
    c = compress.open_compressed(cleaned)
    assert len(c.members) == 2 and c.content == b"onetwo" and c.padding == 0
    assert all(cleaned[m.start:m.data_start] == compress.NEUTRAL_GZIP_HEADER for m in c.members)


@pytest.mark.parametrize("wrap, module, decompress", [
    (lambda d: gz.gzip(d, name=b"backup.tar", mtime=JAN_2024), gzipformat, gzip.decompress),
    (bzip2.compress, bzip2format, bz2.decompress),
    (xz.xz, xzformat, lzma.decompress),
])
def test_tar_inside_is_cleaned(wrap, module, decompress):
    cleaned = module.strip(wrap(TAR))
    inner = decompress(cleaned)
    assert tar_names(inner) == ["docs/a.txt"]
    assert b"jane.doe" not in inner and b"nightly" not in inner
    assert ft.detect(cleaned) == ft.detect(wrap(TAR))
    assert compress.open_compressed(cleaned).content == inner


def test_already_clean_tar_keeps_its_compressed_bytes():
    from metaex.formats import tar as tarformat

    clean_tar = tarformat.strip(TAR)
    for data in (bzip2.compress(clean_tar), xz.xz(clean_tar)):
        assert compress.strip(data, compress.kind_of(data)) == data
    data = gz.gzip(clean_tar, mtime=1)
    assert gzipformat.strip(data)[10:] == data[10:]


def test_bzip2_keeps_the_block_size_and_drops_trailing_data():
    data = bzip2.compress(TAR, level=3) + b"appended"
    cleaned = bzip2format.strip(data)
    assert cleaned[:4] == b"BZh3" and not cleaned.endswith(b"appended")
    plain = bzip2.compress(b"single file") + bytes(16)
    assert bzip2format.strip(plain) == bzip2.compress(b"single file")


def test_xz_keeps_the_check_and_drops_padding():
    cleaned = xzformat.strip(xz.xz(TAR, check=xz.CHECK_NONE))
    assert compress.open_compressed(cleaned).members[0].check == 0
    data = xz.xz(b"single file", streams=2, padding=4)
    cleaned = xzformat.strip(data)
    c = compress.open_compressed(cleaned)
    assert c.content == b"single file" and c.padding == 0 and len(c.members) == 2


def test_report_of_the_cleaned_file():
    data = gz.gzip(TAR, name=b"backup.tar", mtime=JAN_2024, comment=b"c") + b"x"
    keys = [s.key for s in gzipformat.parse(gzipformat.strip(data))["sections"]]
    assert keys == ["compress_contents", "archive_contents"]


def test_removed_sizes_and_warnings():
    data = gz.gzip(TAR) + b"appended"
    sizes = gzipformat.removed_sizes(data)
    assert sizes["compress_hidden"] == 8
    assert "removed 1 operating system file(s): docs/.DS_Store" in gzipformat.strip_warnings(data)


def test_damaged_files_are_refused():
    with pytest.raises(MalformedFileError):
        gzipformat.strip(gz.gzip(b"x")[:-2])
    with pytest.raises(MalformedFileError):
        xzformat.strip(xz.xz(b"x") + b"garbage!")
    with pytest.raises(MalformedFileError):
        gzipformat.strip(gz.gzip(build_tar([file("../evil", b"x")])))


@pytest.mark.parametrize("name, expected", [
    ("backup.tar.gz", "backup_clean.tar.gz"),
    ("Backup.TAR.GZ", "Backup_clean.TAR.GZ"),
    ("backup.tar.bz2", "backup_clean.tar.bz2"),
    ("backup.tar.xz", "backup_clean.tar.xz"),
    ("backup.tgz", "backup_clean.tgz"),
    ("photo.jpg.gz", "photo_clean.jpg.gz"),
    ("notes.gz", "notes_clean.gz"),
    (".tar.gz", ".tar_clean.gz"),
])
def test_output_name_keeps_double_extensions(name, expected):
    assert default_output_path(os.path.join("d", name), ft.GZIP) == os.path.join("d", expected)
