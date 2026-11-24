"""Risk levels of archive fields (core/risk.py, ARCHIVE_SECTIONS)."""

from __future__ import annotations

import pytest

from helpers.appledouble import appledouble, sample_appledouble
from helpers.zip import Entry, build_zip, dos_stamp, unix_ids, unix_times
from metaex.core.risk import CRITICAL, HIGH, LOW, MEDIUM, assess, classify
from metaex.formats import zip as zipformat

UNIX = 3 << 8 | 20


def levels(data: bytes) -> dict[tuple[str, str], str]:
    found = assess(zipformat.report(data)["sections"])
    return {(f.section_key, f.label): f.level for f in found}


def test_kitchen_sink_levels():
    date, time = dos_stamp(2024, 5, 1, 10, 30)
    data = build_zip([
        Entry("home/jane.doe/plan.txt", b"x", date=date, time=time,
              local_extra=unix_times(1714559400) + unix_ids(1000, 1000),
              comment=b"see jane.doe@example.com"),
        Entry("notes.txt", b"x", comment=b"second draft"),
        Entry("secret.txt", b"x", created_by=10 << 8 | 20),
        Entry("private.txt", b"x", created_by=UNIX, external_attr=0o100600 << 16),
        Entry("__MACOSX/._plan.txt", sample_appledouble()),
        Entry(".DS_Store", b"\x00\x00\x00\x01Bud1"),
        Entry("link", b"/home/jane.doe/secret", created_by=UNIX, external_attr=0o120777 << 16),
        Entry("rel", b"notes.txt", created_by=UNIX, external_attr=0o120777 << 16),
    ], comment=b"Backup", suffix=b"hidden")
    found = levels(data)
    assert found[("archive_hidden", "Data after the end of the archive")] == CRITICAL
    assert found[("archive_paths", "home/jane.doe/plan.txt")] == CRITICAL
    assert found[("archive_xattrs", "plan.txt: com.apple.quarantine")] == CRITICAL
    assert found[("archive_xattrs", "plan.txt: com.apple.metadata:kMDItemWhereFroms")] == \
        CRITICAL
    assert found[("archive_comments", "Comment on home/jane.doe/plan.txt")] == CRITICAL  # e-mail
    assert found[("archive_comments", "Comment on notes.txt")] == HIGH
    assert found[("archive_comments", "Archive comment")] == HIGH
    assert found[("archive_times", "home/jane.doe/plan.txt")] == HIGH
    assert found[("archive_junk", ".DS_Store")] == HIGH
    assert found[("archive_junk", "__MACOSX/._plan.txt")] == HIGH
    assert found[("archive_owners", "Owner user/group IDs")] == MEDIUM
    assert found[("archive_system", "Created on")] == MEDIUM
    assert found[("archive_system", "Permissions")] == MEDIUM
    assert found[("archive_links", "link (symlink)")] == CRITICAL
    assert found[("archive_links", "rel (symlink)")] == LOW


def test_contents_section_is_not_a_finding():
    found = assess(zipformat.report(build_zip([Entry("a.txt", b"x")]))["sections"])
    assert found == []


def test_other_extended_attributes_are_low():
    data = build_zip([Entry("._a", appledouble([("com.apple.lastuseddate#PS", b"\x01" * 16)]))])
    found = levels(data)
    assert found[("archive_xattrs", "a: com.apple.lastuseddate#PS")] == LOW


@pytest.mark.parametrize("section, key, value, level", [
    ("archive_owners", "owner_names", "jane.doe/staff (3 entries)", HIGH),
    ("archive_owners", "owner_ids", "1000/1000 (3 entries)", MEDIUM),
    ("archive_extra", "extra.a.0", "4 bytes", LOW),
    ("archive_extra", "extra.a.0", "jane.doe@example.com", CRITICAL),
    ("archive_extra", "extra.a.0", r"C:\Users\jane.doe\Desktop\a.txt", CRITICAL),
    ("archive_links", "link.a", "../b", LOW),
    ("archive_links", "link.a", "/Users/jane.doe/b", CRITICAL),
    ("archive_xattrs", "xattr.a.com.example.tags", "red", LOW),
])
def test_classify(section, key, value, level):
    assert classify(section, key, "", value) == level
