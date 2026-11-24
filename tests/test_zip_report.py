"""What a plain ZIP archive reveals (formats/zip.py report)."""

from __future__ import annotations

import struct

import pytest

from helpers.appledouble import sample_appledouble
from helpers.zip import (DEFLATED, FLAG_ENCRYPTED, Entry, build_zip, dos_stamp, extra,
                         ntfs_times, unix_ids, unix_times)
from metaex.core.errors import MalformedFileError
from metaex.formats import zip as zipformat

UNIX = 3 << 8 | 20
NTFS = 10 << 8 | 20


def sections(data: bytes) -> dict:
    return {s.key: s for s in zipformat.report(data)["sections"]}


def values(section) -> dict:
    return {f.key: f.value for f in section.fields}


def test_plain_archive_reveals_nothing():
    found = zipformat.report(build_zip([Entry("a.txt", b"hello"), Entry("dir/")]))
    keys = [s.key for s in found["sections"]]
    assert keys == ["archive_contents"]
    contents = values(found["sections"][0])
    assert contents["entries"] == 2 and contents["format"] == "ZIP"
    assert contents["count.file"] == 1 and contents["count.folder"] == 1
    assert found["warnings"] == [] and found["complete"]


def test_entry_times_from_dos_time_and_extra_fields():
    date, time = dos_stamp(2024, 5, 1, 10, 30, 0)
    data = build_zip([Entry("a.txt", b"x", date=date, time=time,
                            local_extra=unix_times(1714559400) + ntfs_times(
                                133590330000000000, 133590330000000000, 133590330000000000),
                            central_extra=unix_times(1714559400))])
    times = values(sections(data)["archive_times"])["times.a.txt"]
    assert "modified (local time) 2024-05-01T10:30:00" in times
    assert "modified (extended timestamp) 2024-05-01T10:30:00+00:00" in times
    assert "created (NTFS) 2024-05-01" in times
    assert times.count("extended timestamp") == 1  # the central copy isn't repeated


def test_neutral_time_is_not_reported():
    assert "archive_times" not in sections(build_zip([Entry("a.txt", b"x")]))


def test_owner_ids_from_unix_extra_field():
    data = build_zip([Entry("a.txt", b"x", local_extra=unix_ids(1000, 1000)),
                      Entry("b.txt", b"y", local_extra=unix_ids(501, 20))])
    owners = values(sections(data)["archive_owners"])
    assert owners["owner_ids"] == "1000/1000 (1 entry), 501/20 (1 entry)"


def test_old_unix_extra_fields():
    data = build_zip([Entry("a.txt", b"x", local_extra=extra(0x5855, struct.pack(
        "<IIHH", 1714559400, 1714559400, 1000, 100)))])
    found = sections(data)
    assert "accessed (Unix extra field) 2024-05-01" in values(found["archive_times"])[
        "times.a.txt"]
    assert values(found["archive_owners"])["owner_ids"] == "1000/100 (1 entry)"


def test_operating_system_and_permissions():
    data = build_zip([
        Entry("ntfs.txt", b"x", created_by=NTFS),
        Entry("dos.txt", b"x"),
        Entry("plain.txt", b"x", created_by=UNIX, external_attr=0o100644 << 16),
        Entry("run.sh", b"x", created_by=UNIX, external_attr=0o100755 << 16),
        Entry("secret.txt", b"x", created_by=UNIX, external_attr=0o100600 << 16),
        Entry("dir/", created_by=UNIX, external_attr=(0o40700 << 16) | 0x10),
    ])
    system = values(sections(data)["archive_system"])
    assert system["created_by"] == "Windows NTFS (1 entry)"
    assert system["permissions"] == "600 (file) (1 entry), 700 (folder) (1 entry)"


def test_windows_attributes():
    data = build_zip([Entry("a.txt", b"x", external_attr=0x02 | 0x04 | 0x20)])
    assert values(sections(data)["archive_extra"]) == {"extra.a.txt.0": "hidden, system"}


def test_comments():
    data = build_zip([Entry("a.txt", b"x", comment=b"for jane.doe@example.com")],
                     comment=b"Backup of Jane's laptop")
    comments = values(sections(data)["archive_comments"])
    assert comments == {"archive_comment": "Backup of Jane's laptop",
                        "comment.a.txt": "for jane.doe@example.com"}


def test_os_junk_entries_and_their_attributes():
    data = build_zip([
        Entry("plans/floor.pdf", b"%PDF"),
        Entry("__MACOSX/"), Entry("__MACOSX/plans/"),
        Entry("__MACOSX/plans/._floor.pdf", sample_appledouble()),
        Entry("plans/.DS_Store", b"\x00\x00\x00\x01Bud1"),
        Entry("plans/Thumbs.db", b"\xd0\xcf\x11\xe0"),
        Entry("plans/desktop.ini", b"[.ShellClassInfo]"),
        Entry("plans/._notes", b"not an AppleDouble file at all"),
    ])
    found = zipformat.report(data)
    by_key = {s.key: s for s in found["sections"]}
    junk = values(by_key["archive_junk"])
    assert sorted(junk) == ["junk.__MACOSX", "junk.__MACOSX/plans",
                            "junk.__MACOSX/plans/._floor.pdf", "junk.plans/.DS_Store",
                            "junk.plans/Thumbs.db", "junk.plans/desktop.ini"]
    assert "plans/floor.pdf" in junk["junk.__MACOSX/plans/._floor.pdf"]
    xattrs = {f.label: f.value for f in by_key["archive_xattrs"].fields}
    assert "downloaded with Safari" in xattrs["plans/floor.pdf: com.apple.quarantine"]
    assert "downloads.example.com" in xattrs[
        "plans/floor.pdf: com.apple.metadata:kMDItemWhereFroms"]
    assert any(w.startswith("CRITICAL") and "downloaded" in w for w in found["warnings"])


def test_appledouble_next_to_the_file():
    data = build_zip([Entry("floor.pdf", b"%PDF"), Entry("._floor.pdf", sample_appledouble())])
    found = sections(data)
    assert "junk.._floor.pdf" in values(found["archive_junk"])
    assert any(f.label.startswith("floor.pdf: ") for f in found["archive_xattrs"].fields)


def test_names_that_reveal_a_user_folder():
    data = build_zip([Entry("home/jane.doe/notes.txt", b"x"),
                      Entry("Users/jane.doe/Desktop/plan.txt", b"x"),
                      Entry("site/home/index.html", b"x"),
                      Entry("Users/Shared/readme.txt", b"x")])
    paths = values(sections(data)["archive_paths"])
    assert sorted(paths) == ["name.Users/jane.doe/Desktop/plan.txt", "name.home/jane.doe/notes.txt"]
    assert paths["name.home/jane.doe/notes.txt"] == "user folder of 'jane.doe'"


@pytest.mark.parametrize("target, problem", [
    ("notes.txt", None),
    ("../docs/notes.txt", None),
    ("../../etc/passwd", "outside the archive"),
    ("/etc/passwd", "absolute path"),
    ("/home/jane.doe/secret", "absolute path"),
    ("C:/Users/jane.doe/x", "absolute path"),
])
def test_symbolic_links(target, problem):
    data = build_zip([Entry("dir/link", target.encode(), created_by=UNIX,
                            external_attr=0o120777 << 16)])
    found = zipformat.report(data)
    links = {s.key: s for s in found["sections"]}["archive_links"]
    assert links.fields[0].value == target
    assert links.fields[0].note is None if problem is None else problem in links.fields[0].note
    if problem is None:
        assert found["warnings"] == []
    else:
        assert any(problem in w and "refuses" in w for w in found["warnings"])


def test_hidden_data_before_between_and_after():
    data = build_zip([Entry("a.txt", b"x"), Entry("b.txt", b"y")], prefix=b"PREFIX",
                     between={0: b"GAP"}, suffix=b"SUFFIX")
    found = zipformat.report(data)
    hidden = {f.label: f.value for f in {s.key: s for s in found["sections"]}[
        "archive_hidden"].fields}
    assert hidden == {"Data before the first entry": b"PREFIX",
                      "Data between entries 'a.txt' and 'b.txt'": b"GAP",
                      "Data after the end of the archive": b"SUFFIX"}
    assert any(w.startswith("CRITICAL") and "15 bytes" in w for w in found["warnings"])


def test_prepended_data_without_fixed_offsets():
    data = build_zip([Entry("a.txt", b"x")], prefix=b"#!/bin/sh\nexit 0\n",
                     fix_prefix_offsets=False)
    hidden = sections(data)["archive_hidden"]
    assert hidden.fields[0].value == b"#!/bin/sh\nexit 0\n"


def test_encrypted_entries_are_still_reported():
    date, time = dos_stamp(2024, 5, 1)
    data = build_zip([Entry("secret.txt", b"x", flags=FLAG_ENCRYPTED, date=date, time=time)])
    found = zipformat.report(data)
    assert "archive_times" in {s.key for s in found["sections"]}
    assert any("encrypted" in w for w in found["warnings"])


def test_unsupported_method_is_still_reported():
    data = build_zip([Entry("a.txt", b"x", method=12, compressed=b"BZh9")])
    assert any("method 12" in w for w in zipformat.report(data)["warnings"])


def test_signed_archives_are_warned_about():
    data = build_zip([Entry("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n"),
                      Entry("META-INF/CERT.SF", b"x"), Entry("META-INF/CERT.RSA", b"x")])
    assert any("signed" in w for w in zipformat.report(data)["warnings"])
    block = struct.pack("<Q", 24) + struct.pack("<Q", 24) + b"APK Sig Block 42"
    data = build_zip([Entry("classes.dex", b"dex")], before_directory=block)
    assert any("APK signing block" in w for w in zipformat.report(data)["warnings"])


def test_unknown_and_unicode_extra_fields():
    import zlib

    name = "caf\u00e9.txt"
    unicode_path = extra(0x7075, b"\x01" + struct.pack("<I", zlib.crc32(b"cafe.txt"))
                         + name.encode())
    data = build_zip([Entry("cafe.txt", b"x", local_extra=unicode_path + extra(0x4242, b"zz"))])
    extra_values = [f.value for f in sections(data)["archive_extra"].fields]
    assert name in extra_values and "2 bytes" in extra_values


def test_damaged_extra_field_is_reported_not_raised():
    data = build_zip([Entry("a.txt", b"x", local_extra=extra(0x7875, b"\x01\x08"))])
    labels = [f.label for f in sections(data)["archive_extra"].fields]
    assert labels == ["a.txt: Unix user and group (damaged)"]


def test_many_entries_are_counted_not_all_listed():
    date, time = dos_stamp(2024, 5, 1)
    data = build_zip([Entry(f"f{i}.txt", b"x", date=date, time=time) for i in range(60)])
    times = sections(data)["archive_times"]
    assert len(times.fields) == 51 and times.fields[-1].value == "10 more not listed"


def test_structure_lists_entries():
    found = zipformat.report(build_zip([Entry("a.txt", b"hello" * 10, method=DEFLATED)]))
    structure = found["structure"]
    assert structure.get("entry.0").value.startswith("deflate, ")


@pytest.mark.parametrize("data", [
    b"", b"PK\x03\x04", build_zip([Entry("../evil.txt", b"x")]),
    build_zip([Entry("/abs.txt", b"x")]), build_zip([Entry("a.txt", b"x")])[:-5],
])
def test_damaged_or_unsafe_archives_raise(data):
    with pytest.raises(MalformedFileError):
        zipformat.report(data)
