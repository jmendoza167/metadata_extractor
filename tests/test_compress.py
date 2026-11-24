"""Reading gzip, bzip2 and xz files (core/compress.py, formats/gzip.py,
bzip2.py, xz.py)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from helpers import bzip2, gz, xz
from helpers.deflate import repeat_deflate
from helpers.jpeg import sample_photo
from helpers.tar import JAN_2024, build_tar, file
from metaex.core import compress
from metaex.core import filetype as ft
from metaex.core.errors import MalformedFileError
from metaex.core.inflate import Budget
from metaex.core.risk import CRITICAL, HIGH, LOW, MEDIUM, assess
from metaex.core.ziparchive import ArchiveLimitError, ZipLimits
from metaex.formats import bzip2 as bzip2format
from metaex.formats import gzip as gzipformat
from metaex.formats import xz as xzformat

TAR = build_tar([file("home/jane.doe/a.txt", b"hello", uname=b"jane.doe", mtime=JAN_2024)])


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


def sections(found: dict) -> dict:
    return {s.key: {f.key: f.value for f in s.fields} for s in found["sections"]}


# -- detection ------------------------------------------------------------------------------


@pytest.mark.parametrize("data, expected", [
    (gz.gzip(b"x"), ft.GZIP), (bzip2.compress(b"x"), ft.BZIP2), (bzip2.compress(b""), ft.BZIP2),
    (bzip2.compress(b"x", level=1), ft.BZIP2), (xz.xz(b"x"), ft.XZ),
])
def test_detection(data, expected):
    assert ft.detect(data) == expected


def test_bz_without_a_block_is_not_bzip2():
    assert ft.detect(b"BZh9 not really") == ft.UNKNOWN


# -- gzip -------------------------------------------------------------------------------------


def test_gzip_header_fields():
    data = gz.gzip(b"hello", name=b"notes.txt", mtime=JAN_2024, os=3,
                   comment=b"for jane.doe@example.com", extra=gz.subfield(b"AB", b"xyz"),
                   header_crc=True, text=True)
    c = compress.open_compressed(data)
    assert c.content == b"hello" and len(c.members) == 1
    m = c.members[0]
    assert (m.name, m.mtime, m.os, m.comment) == (b"notes.txt", JAN_2024, 3,
                                                   b"for jane.doe@example.com")
    found = sections(gzipformat.parse(data))
    assert found["compress_header"] == {
        "name": "notes.txt", "mtime": "2024-05-01T10:30:00+00:00", "os": "Unix",
        "comment": "for jane.doe@example.com", "extra": "'AB': 3 bytes", "text": "set"}
    assert found["compress_contents"]["inner"] == "a file of unrecognized type"


def test_neutral_gzip_header_reveals_nothing():
    found = sections(gzipformat.parse(gz.gzip(b"hello")))
    assert set(found) == {"compress_contents"}


def test_gzip_members_are_read_in_order():
    data = gz.member(b"one", name=b"a") + gz.member(b"two", mtime=5)
    c = compress.open_compressed(data)
    assert c.content == b"onetwo" and len(c.members) == 2
    found = sections(gzipformat.parse(data))
    assert found["compress_header"] == {"name.1": "a", "mtime.2": "1970-01-01T00:00:05+00:00"}


def test_gzip_trailing_data_and_padding():
    c = compress.open_compressed(gz.gzip(b"x") + bytes(100))
    assert c.padding == 100 and c.trailing is None
    data = gz.gzip(b"x") + b"secret appended"
    c = compress.open_compressed(data)
    assert c.trailing == (len(data) - 15, len(data))
    found = gzipformat.parse(data)
    assert sections(found)["compress_hidden"]["trailing"] == b"secret appended"
    assert found["warnings"][0].startswith("CRITICAL: 15 bytes")


def test_real_deflate_data_is_read():
    import zlib

    z = zlib.compressobj(9, zlib.DEFLATED, -15)
    body = z.compress(b"hello " * 1000) + z.flush()
    assert compress.open_compressed(gz.gzip(b"hello " * 1000, deflate=body)).content == \
        b"hello " * 1000


@pytest.mark.parametrize("data, match", [
    (gz.gzip(b"x")[:5], "truncated"),
    (gz.gzip(b"hello")[:-3], "truncated"),
    (gz.gzip(b"hello")[:-12], "truncated"),
    (gz.gzip(b"x", flags=0x20), "reserved flags"),
    (gz.gzip(b"x", crc=1), "CRC-32"),
    (gz.gzip(b"x", size=7), "size"),
    (gz.gzip(b"x", deflate=b"\xff\xff\xff"), "invalid compressed data"),
    (gz.gzip(b"x", name=b"no end" * 20000)[:200], "no end"),
    (gz.gzip(b"x") + gz.gzip(b"y")[:8], "truncated"),
])
def test_damaged_gzip_is_refused(data, match):
    with pytest.raises(MalformedFileError, match=match):
        compress.open_compressed(data)


def test_bad_header_crc_is_refused():
    data = bytearray(gz.gzip(b"x", name=b"a", header_crc=True))
    data[12] ^= 0xFF  # the name's first byte, covered by the header CRC
    with pytest.raises(MalformedFileError, match="header CRC"):
        compress.open_compressed(bytes(data))


def test_decompression_bomb_is_refused():
    raw, expanded = repeat_deflate(0x20, 8200)  # 2 MiB from 2 KB: ratio about 1000
    data = gz.gzip(expanded, deflate=raw)
    with pytest.raises(ArchiveLimitError, match="decompression bomb"):
        compress.open_compressed(data)


def test_total_size_limit_and_shared_budget():
    data = gz.gzip(b"x" * 5000)
    with pytest.raises(ArchiveLimitError):
        compress.open_compressed(data, ZipLimits(max_total_size=1000))
    budget = Budget(6000)
    compress.open_compressed(data, budget=budget)
    assert budget.remaining == 1000
    with pytest.raises(ArchiveLimitError):
        compress.open_compressed(data, budget=budget)


def test_too_many_members(monkeypatch):
    monkeypatch.setattr(compress, "MAX_MEMBERS", 3)
    with pytest.raises(MalformedFileError, match="members"):
        compress.open_compressed(gz.member(b"a") * 4)


# -- bzip2 ------------------------------------------------------------------------------------


def test_bzip2_streams():
    data = bzip2.compress(b"one", level=5) + bzip2.compress(b"two")
    c = compress.open_compressed(data)
    assert c.content == b"onetwo" and [m.level for m in c.members] == [5, 9]
    assert sections(bzip2format.parse(data))["compress_contents"]["block_size"] == "500 kB"


def test_bzip2_trailing_data():
    data = bzip2.compress(b"x") + b"appended"
    assert compress.open_compressed(data).trailing is not None
    assert compress.open_compressed(bzip2.compress(b"x") + bytes(8)).padding == 8


@pytest.mark.parametrize("cut", [5, 20, -1, -5])
def test_truncated_bzip2_is_refused(cut):
    with pytest.raises(MalformedFileError):
        compress.open_compressed(bzip2.compress(b"hello " * 100)[:cut])


def test_damaged_bzip2_is_refused():
    data = bytearray(bzip2.compress(b"hello " * 100))
    data[20] ^= 0xFF
    with pytest.raises(MalformedFileError):
        compress.open_compressed(bytes(data))


# -- xz ---------------------------------------------------------------------------------------


def test_xz_streams_and_padding():
    data = xz.xz(b"hello world", streams=2, padding=8)
    c = compress.open_compressed(data)
    assert c.content == b"hello world" and len(c.members) == 2 and c.padding == 8
    assert compress.open_compressed(xz.xz(b"x", check=xz.CHECK_NONE)).members[0].check == 0
    assert sections(xzformat.parse(xz.xz(b"x")))["compress_contents"]["check"] == "CRC32"


@pytest.mark.parametrize("data, match", [
    (xz.xz(b"x") + b"garbage!", "not another stream"),
    (xz.xz(b"x") + bytes(3), "multiple of four"),
    (xz.xz(b"x")[:-1], "invalid|truncated"),
    (xz.xz(b"x")[:-8] + b"\x00\x00\x00\x00\x00\x00YZ", "invalid"),
    (xz.xz(b"hello")[:30], "truncated|invalid"),
])
def test_damaged_xz_is_refused(data, match):
    with pytest.raises(MalformedFileError, match=match):
        compress.open_compressed(data)


def test_xz_header_crc_is_checked():
    data = bytearray(xz.xz(b"x"))
    data[8] ^= 1
    with pytest.raises(MalformedFileError, match="CRC"):
        compress.open_compressed(bytes(data))


# -- what is inside -----------------------------------------------------------------------------


@pytest.mark.parametrize("wrap, module", [(gz.gzip, gzipformat), (bzip2.compress, bzip2format),
                                          (xz.xz, xzformat)])
def test_tar_inside_is_reported(wrap, module):
    found = module.parse(wrap(TAR))
    keys = sections(found)
    assert keys["compress_contents"]["inner"] == "TAR"
    assert "archive_owners" in keys and "archive_paths" in keys
    assert found["structure"] is not None


def test_single_file_inside():
    found = sections(gzipformat.parse(gz.gzip(sample_photo(), name=b"photo.jpg")))
    assert found["compress_contents"]["inner"] == "JPEG"


def test_wrong_kind_is_refused():
    with pytest.raises(MalformedFileError):
        gzipformat.parse(xz.xz(b"x"))


def test_risk_levels():
    data = gz.gzip(TAR, name=b"backup.tar", mtime=JAN_2024, os=11, comment=b"Jane's backup",
                   extra=gz.subfield(b"AB", b"x")) + b"appended"
    found = {(f.section_key, f.field_key): f.level for f in assess(gzipformat.parse(data)[
        "sections"])}
    assert found[("compress_header", "mtime")] == HIGH
    assert found[("compress_header", "comment")] == HIGH
    assert found[("compress_header", "os")] == MEDIUM
    assert found[("compress_header", "name")] == LOW
    assert found[("compress_header", "extra")] == LOW
    assert found[("compress_hidden", "trailing")] == CRITICAL
    assert found[("archive_paths", "name.home/jane.doe/a.txt")] == CRITICAL


def test_cli_show_and_risk(tmp_path):
    path = tmp_path / "backup.tar.gz"
    path.write_bytes(gz.gzip(TAR, name=b"backup.tar", mtime=JAN_2024))
    r = metaex("show", "--json", path)
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report["sections"]["file_type"]["fields"]["name"]["value"] == "gzip"
    assert report["sections"]["file_type"]["fields"]["embedded_metadata"]["value"] == "examined"
    assert report["warnings"] == []
    r = metaex("risk", "--json", path)
    summary = json.loads(r.stdout)["summary"]
    assert summary["critical"] == 1 and summary["high"] >= 2


def test_cli_damaged_file_is_reported(tmp_path):
    path = tmp_path / "broken.xz"
    path.write_bytes(xz.xz(b"x") + b"garbage!")
    r = metaex("show", "--json", path)
    assert r.returncode == 0, r.stderr
    assert any("not another stream" in w for w in json.loads(r.stdout)["warnings"])
