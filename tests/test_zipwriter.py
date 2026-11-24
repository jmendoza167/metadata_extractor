"""The deterministic ZIP writer."""

from __future__ import annotations

import io
import zipfile

import pytest

from helpers.zip import DEFLATED, Entry, build_zip, dos_stamp, ntfs_times
from metaex.core.ziparchive import open_archive, report_sections
from metaex.core.zipwriter import copied, new, write_zip

STAMP = dos_stamp(2024, 5, 1, 12, 30)


def messy_archive() -> bytes:
    """An archive with everything the writer must not carry over."""
    return build_zip([
        Entry("word/document.xml", b"<w:document/>" * 20, method=DEFLATED, date=STAMP[0],
              time=STAMP[1], created_by=(3 << 8) | 30, local_extra=ntfs_times(1, 2, 3),
              comment=b"entry note", descriptor=True),
        Entry("[Content_Types].xml", b"<Types/>", date=STAMP[0], time=STAMP[1]),
        Entry("docProps/core.xml", b"<cp:coreProperties/>", method=DEFLATED),
    ], comment=b"archive comment", prefix=b"junk", suffix=b"tail", between={0: b"gap"})


def test_copied_entries_keep_their_compressed_bytes():
    source = open_archive(messy_archive())
    out = open_archive(write_zip(copied(source, e) for e in source.entries))
    for e in source.entries:
        assert out.raw(out.get(e.name)) == source.raw(e)
        assert out.read(e.name) == source.read(e)


def test_headers_reveal_nothing():
    source = open_archive(messy_archive())
    data = write_zip(copied(source, e) for e in source.entries)
    out = open_archive(data)
    assert report_sections(out) == []  # no hidden data, comments, extra fields, times, OS
    assert out.comment == b"" and out.hidden == []
    for e in out.entries:
        assert (e.dos_date, e.dos_time) == (0x0021, 0) and e.created_by == 20
        assert e.version_needed == 20 and e.extra == [] and e.comment == b""
        assert e.external_attr == 0 and e.internal_attr == 0
        assert not e.flags & 0x0008  # no data descriptors
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.testzip() is None
        assert {i.date_time for i in z.infolist()} == {(1980, 1, 1, 0, 0, 0)}


def test_order_is_kept_except_for_the_names_moved_first():
    source = open_archive(messy_archive())
    entries = [copied(source, e) for e in source.entries]
    assert open_archive(write_zip(entries)).names() == [
        "word/document.xml", "[Content_Types].xml", "docProps/core.xml"]
    assert open_archive(write_zip(entries, first=("[Content_Types].xml",))).names() == [
        "[Content_Types].xml", "word/document.xml", "docProps/core.xml"]
    assert open_archive(write_zip(entries, first=("missing",))).names()[0] == "word/document.xml"


def test_opendocument_mimetype_first_and_stored():
    mimetype = new("mimetype", b"application/vnd.oasis.opendocument.text", method=0)
    data = write_zip([new("content.xml", b"<office:document-content/>"), mimetype],
                     first=("mimetype",))
    # The fixed position ODF requires: the bytes "mimetype" + the type at offset 30 and 38.
    assert data[30:38] == b"mimetype"
    assert data[38:38 + 39] == b"application/vnd.oasis.opendocument.text"
    with pytest.raises(ValueError, match="stored uncompressed"):
        write_zip([new("mimetype", b"application/vnd.oasis.opendocument.text")],
                  first=("mimetype",))


def test_new_entries_round_trip():
    data = write_zip([new("a.xml", b"<a/>" * 100), new("b.bin", b"\x00\x01", method=0),
                      new("café.txt", b"x")])
    out = open_archive(data)
    assert out.read("a.xml") == b"<a/>" * 100 and out.get("a.xml").method == DEFLATED
    assert out.read("b.bin") == b"\x00\x01" and out.get("b.bin").method == 0
    assert out.get("café.txt").flags & 0x0800


def test_output_is_deterministic():
    source = open_archive(messy_archive())
    first = write_zip(copied(source, e) for e in source.entries)
    second = write_zip(copied(source, e) for e in open_archive(messy_archive()).entries)
    assert first == second


def test_duplicate_names_are_refused():
    with pytest.raises(ValueError, match="duplicate"):
        write_zip([new("a", b"1"), new("a", b"2")])


def test_unknown_method_is_refused():
    with pytest.raises(ValueError, match="unsupported compression method"):
        new("a", b"1", method=12)


def test_copying_checks_the_entry_first():
    source = open_archive(build_zip([Entry("a", b"data", crc=1)]))
    with pytest.raises(Exception, match="bad CRC-32"):
        copied(source, source.get("a"))


def test_empty_archive():
    assert open_archive(write_zip([])).entries == []


def test_entries_with_a_mode_are_made_by_unix():
    import io
    import zipfile

    from metaex.core.zipwriter import new, write_zip

    data = write_zip([new("dir/", b"", mode=0o40755), new("dir/run", b"x", mode=0o100755),
                      new("plain", b"y")])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = {i.filename: i for i in z.infolist()}
    assert infos["dir/"].create_system == 3
    assert infos["dir/"].external_attr == 0o40755 << 16 | 0x10
    assert infos["dir/run"].external_attr == 0o100755 << 16
    assert infos["plain"].create_system == 0 and infos["plain"].external_attr == 0
