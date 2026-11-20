import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from metaex.core import generic
from metaex.core.errors import FileAccessError
from metaex.core.generic import (
    UNAVAILABLE,
    file_system_section,
    mark_of_the_web_section,
    parse_zone_identifier,
)


@pytest.fixture
def sample(tmp_path):
    p = tmp_path / "sample.txt"
    p.write_bytes(b"x" * 2048)
    return p


def values(section):
    return {f.key: f.value for f in section.fields}


def test_size_fields(sample):
    v = values(file_system_section(sample))
    assert v["size_bytes"] == 2048
    assert v["size"] == "2.0 KiB"
    assert v["path"] == str(sample)


def test_times_are_timezone_aware_local_datetimes(sample):
    v = values(file_system_section(sample))
    for key in ("modified", "accessed"):
        assert isinstance(v[key], datetime)
        assert v[key].tzinfo is not None
        assert v[key].utcoffset() is not None
        datetime.fromisoformat(v[key].isoformat())


def test_modified_time_matches_stat(sample):
    os.utime(sample, (1_000_000_000, 1_700_000_000))
    v = values(file_system_section(sample))
    assert v["modified"].timestamp() == 1_700_000_000
    assert v["accessed"].timestamp() == 1_000_000_000


def test_created_time_on_platforms_that_have_it(sample):
    v = values(file_system_section(sample))
    has_birth = hasattr(os.stat(sample), "st_birthtime") or sys.platform == "win32"
    if has_birth:
        assert isinstance(v["created"], datetime)
    else:
        assert v["created"] == UNAVAILABLE


def fake_stat(**extra):
    base = dict(st_mode=0o100644, st_size=1, st_mtime=0.0, st_atime=0.0, st_ctime=0.0)
    base.update(extra)
    return SimpleNamespace(**base)


def test_created_unavailable_without_birthtime(monkeypatch, sample):
    monkeypatch.setattr(generic.sys, "platform", "linux")
    monkeypatch.setattr(generic.os, "stat", lambda p: fake_stat())
    section = file_system_section(sample)
    created = section.get("created")
    assert created.value == UNAVAILABLE
    assert "does not report" in created.note


def test_birthtime_is_used_when_present(monkeypatch, sample):
    monkeypatch.setattr(generic.os, "stat", lambda p: fake_stat(st_birthtime=1_600_000_000.0))
    assert file_system_section(sample).get("created").value.timestamp() == 1_600_000_000


def test_invalid_timestamp_is_unavailable(monkeypatch, sample):
    monkeypatch.setattr(generic.os, "stat", lambda p: fake_stat(st_mtime=1e300))
    modified = file_system_section(sample).get("modified")
    assert modified.value == UNAVAILABLE
    assert "invalid timestamp" in modified.note


def test_permissions_string(monkeypatch, sample):
    monkeypatch.setattr(generic.os, "stat", lambda p: fake_stat(st_mode=0o100640))
    assert file_system_section(sample).get("permissions").value == "-rw-r----- (640)"


def test_missing_file_raises_clean_error(tmp_path):
    with pytest.raises(FileAccessError):
        file_system_section(tmp_path / "missing")


# --- Mark of the Web ------------------------------------------------------------

ZONE_TEXT = (
    b"[ZoneTransfer]\r\n"
    b"ZoneId=3\r\n"
    b"ReferrerUrl=https://example.com/downloads\r\n"
    b"HostUrl=https://cdn.example.com/file.zip\r\n"
)


def test_parse_zone_identifier():
    assert parse_zone_identifier(ZONE_TEXT) == [
        ("ZoneId", "3"),
        ("ReferrerUrl", "https://example.com/downloads"),
        ("HostUrl", "https://cdn.example.com/file.zip"),
    ]


def test_parse_zone_identifier_utf16_and_garbage():
    text = ZONE_TEXT.decode("ascii")
    assert parse_zone_identifier(b"\xff\xfe" + text.encode("utf-16-le"))[0] == ("ZoneId", "3")
    assert parse_zone_identifier(b"\x00\xff garbage without equals") == []
    assert parse_zone_identifier(b"") == []


def test_parse_zone_identifier_caps_entries():
    raw = b"".join(b"K%d=v\n" % i for i in range(1000))
    assert len(parse_zone_identifier(raw)) == generic.ZONE_IDENTIFIER_MAX_ENTRIES


def test_mark_of_the_web_skipped_on_other_platforms(monkeypatch, sample):
    monkeypatch.setattr(generic.sys, "platform", "linux")
    assert mark_of_the_web_section(sample) is None


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="NTFS streams are Windows-only")


def write_zone_stream(path, data: bytes) -> None:
    try:
        with open(f"{path}:Zone.Identifier", "wb") as f:
            f.write(data)
    except OSError as exc:
        pytest.skip(f"file system does not support alternate data streams: {exc}")


@windows_only
def test_mark_of_the_web_is_read_and_sensitive(sample):
    write_zone_stream(sample, ZONE_TEXT)
    section = mark_of_the_web_section(sample)
    assert section is not None
    v = {f.key: f.value for f in section.fields}
    assert v["zone_id"] == "3 (Internet)"
    assert v["referrer_url"] == "https://example.com/downloads"
    assert v["host_url"] == "https://cdn.example.com/file.zip"
    assert all(f.sensitive for f in section.fields)


@windows_only
def test_no_stream_means_no_section(sample):
    assert mark_of_the_web_section(sample) is None


@windows_only
def test_oversized_stream_is_not_read(sample):
    write_zone_stream(sample, b"A=" + b"x" * (generic.ZONE_IDENTIFIER_MAX_SIZE + 10))
    section = mark_of_the_web_section(sample)
    assert section.fields[0].value == "present but not read"
