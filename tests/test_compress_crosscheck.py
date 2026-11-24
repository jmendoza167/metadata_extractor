"""gzip, bzip2 and xz files written by the stdlib and read back by it after
stripping, plus damaged and mutated files that must be refused cleanly.

Compressed bytes are never compared (they depend on the zlib, bzip2 and
liblzma builds); only decompressed contents are.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import random
import tarfile

import pytest

from helpers import bzip2, gz, xz
from helpers.jpeg import sample_photo
from helpers.tar import JAN_2024
from metaex.core import compress
from metaex.core.errors import MetaexError
from metaex.core.verify import verify


def stdlib_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
        for name, data in (("docs/a.txt", b"alpha " * 500), ("docs/b.bin", bytes(range(256)))):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.uname = len(data), JAN_2024, "jane.doe"
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def stdlib_gzip(data: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile("backup.tar", "wb", fileobj=buf, mtime=JAN_2024) as f:
        f.write(data)
    return buf.getvalue()


COMPRESSORS = {
    "gzip": (stdlib_gzip, gzip.decompress),
    "bzip2": (bz2.compress, bz2.decompress),
    "xz": (lambda d: lzma.compress(d, format=lzma.FORMAT_XZ), lzma.decompress),
}


@pytest.mark.parametrize("kind", COMPRESSORS)
def test_stdlib_files_are_cleaned_and_read_back(kind):
    pack, unpack = COMPRESSORS[kind]
    original = pack(stdlib_tar())
    cleaned = compress.strip(original, kind)
    with tarfile.open(fileobj=io.BytesIO(unpack(cleaned))) as t:
        assert [(m.name, m.uname, m.mtime) for m in t.getmembers()] == [
            ("docs/a.txt", "", 315532800), ("docs/b.bin", "", 315532800)]
        assert t.extractfile("docs/a.txt").read() == b"alpha " * 500
    result = verify(cleaned, original)
    assert result.clean and result.image_data_match


@pytest.mark.parametrize("kind", COMPRESSORS)
def test_stdlib_single_file(kind):
    pack, unpack = COMPRESSORS[kind]
    photo = sample_photo()
    original = pack(photo)
    cleaned = compress.strip(original, kind)
    assert unpack(cleaned) == photo
    if kind != "gzip":
        assert cleaned == original  # nothing to clean: the stream is kept as it is


def test_stdlib_gzip_name_and_time_are_removed():
    original = stdlib_gzip(b"hello")
    assert b"backup.tar" in original
    cleaned = compress.strip(original, "gzip")
    assert b"backup.tar" not in cleaned
    assert gzip.GzipFile(fileobj=io.BytesIO(cleaned)).read() == b"hello"
    with gzip.GzipFile(fileobj=io.BytesIO(cleaned)) as f:
        f.read()
        assert f.mtime == 0


def test_gzip_with_many_small_members():
    original = b"".join(gz.member(bytes([i]) * i, mtime=i) for i in range(1, 40))
    cleaned = compress.strip(original, "gzip")
    assert gzip.decompress(cleaned) == gzip.decompress(original)
    assert len(compress.open_compressed(cleaned).members) == 39


def test_hand_made_bzip2_and_xz_are_read_by_the_stdlib():
    data = bytes(range(256)) * 300 + bytes(5000)
    assert bz2.decompress(bzip2.compress(data)) == data
    assert lzma.decompress(xz.xz(data)) == data
    assert compress.open_compressed(bzip2.compress(data)).content == data
    assert compress.open_compressed(xz.xz(data, streams=3, padding=12)).content == data


# -- damaged and mutated files ---------------------------------------------------------------


@pytest.mark.parametrize("kind", COMPRESSORS)
def test_every_truncation_is_refused(kind):
    pack, _ = COMPRESSORS[kind]
    original = pack(b"hello world " * 50)
    for cut in range(1, len(original), max(1, len(original) // 40)):
        with pytest.raises(MetaexError):
            compress.strip(original[:cut], kind)


@pytest.mark.parametrize("kind", COMPRESSORS)
def test_mutations_never_crash_and_never_pass_unchecked(kind):
    pack, unpack = COMPRESSORS[kind]
    content = b"hello world " * 50
    original = pack(content)
    rng = random.Random(15)
    for _ in range(200):
        data = bytearray(original)
        for _ in range(rng.randint(1, 3)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        data = bytes(data)
        try:
            cleaned = compress.strip(data, kind)
        except MetaexError:
            continue
        # accepted: every integrity check passed, so the contents are intact
        assert compress.open_compressed(cleaned).content == content
        assert verify(cleaned, data).clean


def test_gzip_bomb_inside_a_tar_gz_is_refused():
    from helpers.deflate import repeat_deflate

    raw, expanded = repeat_deflate(0x00, 8200)
    with pytest.raises(MetaexError, match="bomb"):
        compress.strip(gz.gzip(expanded, deflate=raw), "gzip")
