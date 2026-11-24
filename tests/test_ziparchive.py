"""The safe ZIP reader: limits, refusals and hidden data."""

from __future__ import annotations

import io
import random
import struct
import zipfile

import pytest

from helpers.zip import (DEFLATED, FLAG_ENCRYPTED, STORED, Entry, build_zip, central_header,
                         deflate_raw, dos_stamp, extra, local_header, make_zip, ntfs_times,
                         unix_ids, unix_times)
from metaex.core.errors import MalformedFileError
from metaex.core.ziparchive import (ArchiveLimitError, ZipLimits, open_archive,
                                    report_sections)

MIB = 1024 * 1024


def sections(archive):
    return {s.key: {f.key: f.value for f in s.fields} for s in report_sections(archive)}


def labels(archive, key):
    return [f.label for s in report_sections(archive) if s.key == key for f in s.fields]


def stdlib_zip(compression: int, *, create_system: int = 0, external_attr: int = 0) -> bytes:
    """An archive written by zipfile. ZipInfo marks entries as made on MS-DOS
    (0) on Windows but on Unix (3) elsewhere, with permission bits in
    external_attr, so both are set here: the result is the same on every OS."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        for name, text in (("a.txt", "hello " * 100), ("dir/b.xml", "<b/>")):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = create_system
            info.external_attr = external_attr
            z.writestr(info, text)
    return buf.getvalue()


# -- reading ------------------------------------------------------------------------------


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_reads_an_archive_made_by_the_standard_library(compression):
    archive = open_archive(stdlib_zip(compression))
    assert archive.names() == ["a.txt", "dir/b.xml"]
    assert archive.read("a.txt") == b"hello " * 100
    assert archive.read(archive.get("dir/b.xml")) == b"<b/>"
    assert archive.hidden == [] and not archive.zip64
    assert report_sections(archive) == []


def test_an_archive_made_on_unix_by_the_standard_library_says_so():
    archive = open_archive(stdlib_zip(zipfile.ZIP_DEFLATED, create_system=3,
                                      external_attr=0o100644 << 16))
    assert archive.read("a.txt") == b"hello " * 100
    sections = report_sections(archive)
    assert [s.key for s in sections] == ["zip_entries"]
    assert {f.key: f.value for f in sections[0].fields} == {"created_by_os": ["Unix"]}


def test_reads_stored_and_deflated_entries_and_keeps_order():
    data = build_zip([Entry("z.txt", b"last"), Entry("a.xml", b"<a/>" * 50, method=DEFLATED)])
    archive = open_archive(data)
    assert archive.names() == ["z.txt", "a.xml"]
    assert archive.read("a.xml") == b"<a/>" * 50
    assert archive.raw(archive.get("z.txt")) == b"last"


def test_find_ignores_case():
    archive = open_archive(make_zip({"[Content_Types].xml": "<Types/>"}))
    assert archive.find("[content_types].XML").name == "[Content_Types].xml"
    assert archive.get("[content_types].xml") is None


def test_reading_twice_uses_the_budget_once():
    archive = open_archive(make_zip({"a": b"x" * 1000}), ZipLimits(max_total_size=1500))
    assert archive.read("a") == archive.read("a")
    assert archive.budget.remaining == 500


def test_folder_entries_are_allowed():
    archive = open_archive(make_zip({"word/": b"", "word/document.xml": b"<w/>"}))
    assert archive.get("word/").is_dir


def test_utf8_and_cp437_names():
    cp437_name = bytes([0x72, 0x82]) + b"sum" + bytes([0x82]) + b".txt"
    archive = open_archive(build_zip([Entry("café.txt", b"1"), Entry(cp437_name, b"2")]))
    assert archive.names() == ["café.txt", "résumé.txt"]


def test_unknown_entry_is_an_error():
    with pytest.raises(MalformedFileError, match="no entry 'missing'"):
        open_archive(make_zip({"a": b"1"})).read("missing")


# -- data descriptors and ZIP64 -------------------------------------------------------------


@pytest.mark.parametrize("signature", [True, False])
def test_data_descriptors(signature):
    data = build_zip([Entry("a.xml", b"<a/>" * 30, method=DEFLATED, descriptor=True,
                            descriptor_signature=signature), Entry("b", b"2")])
    archive = open_archive(data)
    assert archive.read("a.xml") == b"<a/>" * 30 and archive.read("b") == b"2"
    assert archive.hidden == []


def test_data_descriptor_that_disagrees_is_refused():
    e = Entry("a", b"abc", descriptor=True)
    good = build_zip([e])
    bad = good.replace(b"PK\x07\x08" + good[good.index(b"PK\x07\x08") + 4:][:4],
                       b"PK\x07\x08\x00\x00\x00\x00", 1)
    with pytest.raises(MalformedFileError, match="data descriptor doesn't match"):
        open_archive(bad)


def test_zip64_end_records_and_extra_fields():
    data = build_zip([Entry("a.txt", b"hello", zip64=True),
                      Entry("b.xml", b"<b/>" * 40, method=DEFLATED, zip64=True, descriptor=True)],
                     zip64=True)
    archive = open_archive(data)
    assert archive.zip64
    assert archive.read("a.txt") == b"hello" and archive.read("b.xml") == b"<b/>" * 40
    assert archive.hidden == []
    assert "zip_extra" not in sections(archive)  # the ZIP64 field itself is not reported


def test_zip64_made_by_the_standard_library():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        with z.open(zipfile.ZipInfo("big.bin", (1980, 1, 1, 0, 0, 0)), "w", force_zip64=True) as f:
            f.write(b"0123456789" * 1000)
    assert open_archive(buf.getvalue()).read("big.bin") == b"0123456789" * 1000


def test_zip64_without_locator_is_refused():
    data = build_zip([Entry("a", b"1")], zip64=True)
    loc = data.rindex(b"PK\x06\x07")
    with pytest.raises(MalformedFileError, match="ZIP64 locator is missing"):
        open_archive(data[:loc] + b"XXXX" + data[loc + 4:])


def test_zip64_extra_field_missing_is_refused():
    e = Entry("a", b"1", zip64=True)
    data = build_zip([e])
    i = data.index(b"PK\x01\x02")
    # Point the central directory's extra length at zero: the 0xFFFFFFFF sizes
    # are then left without their ZIP64 values.
    patched = data[:i + 30] + b"\x00\x00" + data[i + 32:]
    with pytest.raises(MalformedFileError):
        open_archive(patched)


# -- zip bombs -------------------------------------------------------------------------------


def test_high_compression_ratio_is_refused():
    zeros = b"\x00" * (2 * MIB)
    archive = open_archive(build_zip([Entry("bomb.xml", zeros, method=DEFLATED)]))
    with pytest.raises(ArchiveLimitError, match="compression ratio"):
        archive.read("bomb.xml")


def test_ratio_is_not_checked_for_small_entries():
    small = b"\x00" * (MIB // 2)  # ratio ~1000, but under the threshold
    assert open_archive(build_zip([Entry("ok", small, method=DEFLATED)])).read("ok") == small


def test_entry_size_limit_does_not_trust_the_declared_size():
    data = b"A" * 5000
    entry = Entry("liar", data, method=DEFLATED, size=10)
    archive = open_archive(build_zip([entry]), ZipLimits(max_entry_size=1000))
    with pytest.raises(ArchiveLimitError, match="limit per entry"):
        archive.read("liar")


def test_declared_size_must_match():
    archive = open_archive(build_zip([Entry("a", b"A" * 100, method=DEFLATED, size=99)]))
    with pytest.raises(MalformedFileError, match="has 100 bytes, but its headers say 99"):
        archive.read("a")


def test_total_size_is_shared_between_entries():
    entries = [Entry(f"part{i}.xml", b"x" * 400, method=DEFLATED) for i in range(5)]
    archive = open_archive(build_zip(entries), ZipLimits(max_total_size=1000))
    archive.read("part0.xml")
    archive.read("part1.xml")
    with pytest.raises(ArchiveLimitError, match="whole archive"):
        archive.read("part2.xml")
    with pytest.raises(ArchiveLimitError):  # the budget is used up: fail fast
        archive.read("part3.xml")


def test_too_many_entries_is_refused_before_reading_them():
    data = build_zip([Entry(f"f{i}", b"") for i in range(11)])
    with pytest.raises(ArchiveLimitError, match="11 entries"):
        open_archive(data, ZipLimits(max_entries=10))


def test_entry_count_claimed_in_the_end_record_is_refused():
    data = build_zip([Entry("a", b"1")], total=60000)
    with pytest.raises(ArchiveLimitError, match="60000 entries"):
        open_archive(data)


def test_nested_archives_are_limited_and_share_the_budget():
    inner = make_zip({"deep.txt": b"y" * 300})
    middle = make_zip({"inner.zip": inner})
    outer = open_archive(make_zip({"middle.zip": middle}), ZipLimits(max_depth=1))
    nested = outer.open_nested("middle.zip")
    assert nested.depth == 1 and nested.budget is outer.budget
    with pytest.raises(ArchiveLimitError, match="nested more than 1 deep"):
        nested.open_nested("inner.zip")


def test_nested_budget_counts_inner_reads():
    inner = make_zip({"deep.txt": b"y" * 600})
    outer = open_archive(make_zip({"inner.zip": inner}), ZipLimits(max_total_size=1200))
    nested = outer.open_nested("inner.zip")
    with pytest.raises(ArchiveLimitError):
        nested.read("deep.txt")


def test_overlapping_entries_are_refused():
    """A second directory entry whose local header lies inside the first
    entry's data (the trick of "overlapping" zip bombs)."""
    inner = Entry("b", b"payload")
    outer = Entry("a", local_header(inner, b"payload") + b"payload")
    data = build_zip([outer])
    cd, eocd = data.index(b"PK"), data.index(b"PK")
    directory = data[cd:eocd] + central_header(inner, b"payload", 30 + len("a"))
    end = b"PK" + struct.pack("<HHHHIIH", 0, 0, 2, 2, len(directory), cd, 0)
    with pytest.raises(MalformedFileError, match="overlap"):
        open_archive(data[:cd] + directory + end)


# -- names ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name, message", [
    ("../evil.txt", "path traversal"),
    ("word/../../evil.txt", "path traversal"),
    ("/etc/passwd", "absolute path"),
    ("C:/Windows/evil.dll", "drive letter"),
    ("c:evil", "drive letter"),
    ("word\\document.xml", "backslash"),
    ("word//document.xml", "empty"),
    ("./document.xml", "empty"),
    ("a\x00b", "control characters"),
    ("a\nb", "control characters"),
    ("", "empty name"),
])
def test_dangerous_names_are_refused(name, message):
    with pytest.raises(MalformedFileError, match=message):
        open_archive(make_zip({name: b"x"}))


@pytest.mark.parametrize("names", [("a.xml", "a.xml"), ("Word/Document.xml", "word/document.xml")])
def test_duplicate_names_are_refused(names):
    data = build_zip([Entry(names[0], b"1"), Entry(names[1], b"2")])
    with pytest.raises(MalformedFileError, match="two entries named"):
        open_archive(data)


# -- encryption and methods ---------------------------------------------------------------------


@pytest.mark.parametrize("entry", [
    Entry("secret.xml", b"x", flags=FLAG_ENCRYPTED),
    Entry("secret.xml", b"x", flags=0x0040),
    Entry("secret.xml", b"x", method=99, compressed=b"x"),
])
def test_encrypted_entries_are_refused(entry):
    with pytest.raises(MalformedFileError, match="encrypted"):
        open_archive(build_zip([entry]))


def test_unsupported_method_is_refused():
    with pytest.raises(MalformedFileError, match="compression method 12"):
        open_archive(build_zip([Entry("a", b"x", method=12, compressed=b"BZh")]))


def test_multi_disk_archive_is_refused():
    with pytest.raises(MalformedFileError, match="several disks"):
        open_archive(build_zip([Entry("a", b"1")], disk=1))


# -- damaged archives ---------------------------------------------------------------------------


@pytest.mark.parametrize("local, message", [
    ({"name": "other.xml"}, "name"),
    ({"method": DEFLATED}, "compression method"),
    ({"flags": 0x0002}, "flags"),
    ({"crc": 1234}, "CRC-32"),
    ({"size": 99, "csize": 99}, "sizes"),
])
def test_local_and_central_headers_must_match(local, message):
    with pytest.raises(MalformedFileError, match=f"local header says {message}"):
        open_archive(build_zip([Entry("a.xml", b"<a/>", local=local)]))


def test_bad_crc_is_refused_on_read():
    archive = open_archive(build_zip([Entry("a", b"data", crc=0x1234)]))
    with pytest.raises(MalformedFileError, match="bad CRC-32"):
        archive.read("a")


def test_bytes_hidden_after_the_deflate_stream_are_refused():
    payload = deflate_raw(b"hello") + b"SECRET"
    archive = open_archive(build_zip([Entry("a", b"hello", method=DEFLATED, compressed=payload)]))
    with pytest.raises(MalformedFileError, match="hides 6 bytes"):
        archive.read("a")


def test_incomplete_deflate_stream():
    payload = deflate_raw(b"hello world" * 10)[:-3]
    archive = open_archive(build_zip([Entry("a", b"hello world" * 10, method=DEFLATED,
                                            compressed=payload)]))
    with pytest.raises(MalformedFileError, match="incomplete|invalid"):
        archive.read("a")


def test_invalid_deflate_data():
    archive = open_archive(build_zip([Entry("a", b"x", method=DEFLATED, compressed=b"\xff" * 8)]))
    with pytest.raises(MalformedFileError, match="invalid compressed data"):
        archive.read("a")


def test_stored_sizes_must_agree():
    with pytest.raises(MalformedFileError, match="stored uncompressed"):
        open_archive(build_zip([Entry("a", b"abc", csize=2, local={"csize": 2})]))


def test_no_end_record():
    with pytest.raises(MalformedFileError, match="truncated"):
        open_archive(make_zip({"a": b"1"})[:-22])


def test_trailing_bytes_in_the_central_directory_are_refused():
    with pytest.raises(MalformedFileError, match="bytes after its"):
        open_archive(build_zip([Entry("a", b"1")], directory_tail=b"\x00" * 8))


def test_truncated_archive_at_every_length():
    data = build_zip([Entry("word/document.xml", b"<w:document/>" * 20, method=DEFLATED),
                      Entry("b.txt", b"hello", comment=b"note")], comment=b"archive")
    for n in range(len(data)):
        try:
            archive = open_archive(data[:n])
            for name in archive.names():
                archive.read(name)
        except MalformedFileError:
            continue


def test_mutated_archives_never_crash():
    data = build_zip([Entry("[Content_Types].xml", b"<Types/>" * 30, method=DEFLATED),
                      Entry("word/document.xml", b"<w:t>hi</w:t>" * 30, method=DEFLATED,
                            descriptor=True),
                      Entry("docProps/core.xml", b"<cp/>", zip64=True)], comment=b"c")
    rng = random.Random(6)
    for _ in range(1500):
        mutated = bytearray(data)
        for _ in range(rng.randint(1, 4)):
            mutated[rng.randrange(len(mutated))] = rng.randrange(256)
        try:
            archive = open_archive(bytes(mutated))
            for name in archive.names():
                archive.read(name)
            report_sections(archive)
        except MalformedFileError:
            pass


# -- hidden data ------------------------------------------------------------------------------


def test_prepended_data_with_offsets_updated():
    archive = open_archive(build_zip([Entry("a", b"1")], prefix=b"MZ stub " * 4))
    assert [(h.where, h.size) for h in archive.hidden] == [("before the first entry", 32)]
    assert archive.read("a") == b"1"
    assert sections(archive)["zip_hidden"]["range_1"] == b"MZ stub " * 4


def test_prepended_data_without_updated_offsets():
    data = b"#!/bin/sh\nexit\n" + make_zip({"a": b"1", "b": b"22"})
    archive = open_archive(data)
    assert archive.read("b") == b"22"
    assert [(h.where, h.size) for h in archive.hidden] == [("before the first entry", 15)]


def test_data_between_entries_and_before_the_directory():
    archive = open_archive(build_zip([Entry("a", b"1"), Entry("b", b"2")],
                                     between={0: b"HIDDEN"}, before_directory=b"MORE"))
    assert [(h.where, h.size) for h in archive.hidden] == [
        ("between entries 'a' and 'b'", 6),
        ("after the last entry ('b'), before the central directory", 4)]
    assert labels(archive, "zip_hidden") == [
        "Data between entries 'a' and 'b'",
        "Data after the last entry ('b'), before the central directory"]


def test_appended_data():
    archive = open_archive(make_zip({"a": b"1"}) + b"appended secret")
    assert [(h.where, h.size) for h in archive.hidden] == [("after the end of the archive", 15)]


def test_data_between_the_zip64_end_records_is_refused():
    data = build_zip([Entry("a", b"1")], zip64=True)
    loc = data.rindex(b"PK")
    with pytest.raises(MalformedFileError, match="ZIP64 end record size"):
        open_archive(data[:loc] + b"GAP!" + data[loc:])


def test_archive_comment_and_entry_comments():
    archive = open_archive(build_zip([Entry("a", b"1", comment=b"made by Jane")],
                                     comment=b"Jane Doe's files"))
    found = sections(archive)
    assert found["zip_comment"] == {"comment": "Jane Doe's files"}
    assert found["zip_hidden"] == {"entry_comment.a": "made by Jane"}


def test_extra_fields_are_reported():
    filetime = 133_590_402_000_000_000  # 2024-05-01T12:30:00Z
    archive = open_archive(build_zip([
        Entry("a.xml", b"1", local_extra=ntfs_times(filetime, filetime, filetime)),
        Entry("b.xml", b"2", local_extra=unix_times(1714566600) + unix_ids(1000, 100),
              central_extra=unix_times(1714566600)),
        Entry("c.xml", b"3", local_extra=extra(0x4242, b"??")),
    ]))
    found = sections(archive)["zip_extra"]
    assert found["a.xml.local.0x000a"] == ("modified 2024-05-01T12:30:00+00:00, accessed "
                                           "2024-05-01T12:30:00+00:00, created "
                                           "2024-05-01T12:30:00+00:00")
    assert found["b.xml.local.0x5455"] == "modified 2024-05-01T12:30:00+00:00"
    assert found["b.xml.local.0x7875"] == "user ID 1000, group ID 100"
    assert found["b.xml.central.0x5455"] == "modified 2024-05-01T12:30:00+00:00"
    assert found["c.xml.local.0x4242"] == "2 bytes"
    assert "c.xml: unknown extra field 0x4242 (local)" in labels(archive, "zip_extra")


def test_damaged_extra_field_is_refused():
    with pytest.raises(MalformedFileError, match="stray bytes"):
        open_archive(build_zip([Entry("a", b"1", local_extra=b"\x01\x02")]))


def test_entry_times_and_operating_system():
    date, time = dos_stamp(2024, 5, 1, 12, 30, 0)
    archive = open_archive(build_zip([
        Entry("a", b"1", date=date, time=time, created_by=(3 << 8) | 30),
        Entry("b", b"2"),
    ]))
    found = sections(archive)["zip_entries"]
    assert found["entry_times"] == "2024-05-01T12:30:00 (1 of 2 entries)"
    assert found["created_by_os"] == ["Unix"]


def test_office_style_archives_report_nothing():
    """Word writes 1980-01-01 00:00 times and MS-DOS as the system."""
    archive = open_archive(build_zip([Entry("a", b"1", created_by=45), Entry("b", b"2")]))
    assert report_sections(archive) == []


def test_many_extra_fields_are_counted_not_all_listed():
    entries = [Entry(f"f{i}", b"", local_extra=unix_times(0)) for i in range(60)]
    found = sections(open_archive(build_zip(entries)))["zip_extra"]
    assert len(found) == 51 and found["more"] == "10 more not listed"


def test_methods_are_stored_or_deflated_only():
    assert {e.method for e in open_archive(build_zip([Entry("a", b"1"), Entry(
        "b", b"2", method=DEFLATED)])).entries} == {STORED, DEFLATED}
