"""AppleDouble files and binary property lists (core/appledouble.py)."""

from __future__ import annotations

import struct

import pytest

from helpers.appledouble import (QUARANTINE, SAMPLE_QUARANTINE, SAMPLE_WHERE_FROMS, WHERE_FROMS,
                                 appledouble, bplist, sample_appledouble)
from metaex.core import appledouble as ad
from metaex.core.errors import MalformedFileError


def test_reads_the_attributes_macos_writes():
    parsed = ad.parse_appledouble(sample_appledouble())
    assert parsed.error is None
    names = [n for n, _ in parsed.xattrs]
    assert names == [QUARANTINE, WHERE_FROMS]
    values = dict(parsed.xattrs)
    assert values[QUARANTINE] == SAMPLE_QUARANTINE
    assert ad.parse_bplist(values[WHERE_FROMS]) == SAMPLE_WHERE_FROMS


def test_describes_quarantine_and_where_froms():
    text = ad.describe(QUARANTINE, SAMPLE_QUARANTINE)
    assert "downloaded with Safari" in text and "2024-05-01T" in text
    assert "3E2F1A44" in text
    urls = ad.describe(WHERE_FROMS, bplist(SAMPLE_WHERE_FROMS))
    assert SAMPLE_WHERE_FROMS[0] in urls and SAMPLE_WHERE_FROMS[1] in urls


def test_finder_info_and_resource_fork_are_listed():
    data = appledouble(finder_info=b"TEXTttxt" + bytes(24), resource_fork=b"icon" * 10)
    parsed = ad.parse_appledouble(data)
    values = dict(parsed.xattrs)
    assert "type 'TEXT'" in ad.describe("com.apple.FinderInfo", values["com.apple.FinderInfo"])
    assert ad.describe("com.apple.ResourceFork",
                       values["com.apple.ResourceFork"]) == "resource fork, 40 bytes"


def test_empty_finder_info_is_not_an_attribute():
    assert ad.parse_appledouble(appledouble()).xattrs == []


def test_is_appledouble():
    assert ad.is_appledouble(sample_appledouble())
    assert not ad.is_appledouble(b"\x00\x05\x16")
    assert not ad.is_appledouble(b"hello world, this is not it at all")


@pytest.mark.parametrize("cut", [4, 20, 30, 60, 90, 130])
def test_truncated_files_give_an_error_not_an_exception(cut):
    parsed = ad.parse_appledouble(sample_appledouble()[:cut])
    assert parsed.error is not None


def test_attribute_outside_the_file_is_an_error():
    data = bytearray(sample_appledouble())
    struct.pack_into(">I", data, 120, 0xFFFFFF)  # first attribute's offset
    assert ad.parse_appledouble(bytes(data)).error is not None


def test_too_many_entries_and_attributes_are_refused():
    data = bytearray(sample_appledouble())
    struct.pack_into(">H", data, 24, 60000)
    assert "entries" in ad.parse_appledouble(bytes(data)).error
    data = bytearray(sample_appledouble())
    struct.pack_into(">H", data, 118, 60000)
    assert "attributes" in ad.parse_appledouble(bytes(data)).error


def test_binary_values_are_not_shown_as_text():
    assert ad.describe("com.example.blob", b"\x00\x01\x02\xff") == "4 bytes of binary data"
    assert ad.describe("com.example.text", b"hello\x00") == "hello"


def test_long_values_are_shortened():
    assert len(ad.describe("com.example.text", b"a" * 10_000)) == ad.MAX_SHOWN


# -- binary property lists ------------------------------------------------------------------


def test_bplist_values():
    assert ad.parse_bplist(bplist(["a", 5, True, ["b"]])) == ["a", 5, True, ["b"]]
    assert ad.parse_bplist(bplist("x" * 300)) == "x" * 300


@pytest.mark.parametrize("data", [
    b"", b"bplist00", b"bplist00" + bytes(31), b"notplist" + bytes(40),
])
def test_bplist_rejects_non_lists(data):
    with pytest.raises(MalformedFileError):
        ad.parse_bplist(data)


def test_bplist_object_containing_itself_is_refused():
    # one array whose only element is itself
    body = b"bplist00" + b"\xa1\x00"
    table = len(body)
    data = body + struct.pack(">H", 8) + bytes(6) + struct.pack(">BBQQQ", 2, 1, 1, 0, table)
    with pytest.raises(MalformedFileError):
        ad.parse_bplist(data)


def test_bplist_shared_objects_are_bounded():
    # 10 arrays, each holding the next one 4 times: 4**10 visits if unbounded
    n = 10
    objects = [bytes([0xA4] + [i + 1] * 4) for i in range(n)] + [b"\x51a"]
    body = bytearray(b"bplist00")
    offsets = []
    for obj in objects:
        offsets.append(len(body))
        body += obj
    table = len(body)
    body += b"".join(struct.pack(">H", o) for o in offsets)
    body += bytes(6) + struct.pack(">BBQQQ", 2, 1, len(objects), 0, table)
    with pytest.raises(MalformedFileError, match="too many times"):
        ad.parse_bplist(bytes(body))


def test_bplist_bad_reference_is_refused():
    body = b"bplist00" + b"\xa1\x07"
    table = len(body)
    data = body + struct.pack(">H", 8) + bytes(6) + struct.pack(">BBQQQ", 2, 1, 1, 0, table)
    with pytest.raises(MalformedFileError):
        ad.parse_bplist(data)


def test_damaged_where_froms_is_described_as_damaged():
    assert ad.describe(WHERE_FROMS, b"bplist00" + bytes(10)).startswith("damaged")
