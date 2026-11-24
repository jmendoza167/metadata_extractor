"""Reading TAR archives (core/tar.py) and what they reveal (formats/tar.py)."""

from __future__ import annotations

import io
import tarfile

import pytest

from helpers.appledouble import sample_appledouble
from helpers.tar import (GNU, JAN_2024, V7, Header, base256, build_tar, file, folder, gnu_long,
                         hardlink, pax, symlink)
from metaex.core import tar as tarcore
from metaex.core.errors import MalformedFileError, UnsupportedFormatError
from metaex.formats import tar as tarformat


def sections(data: bytes) -> dict:
    return {s.key: s for s in tarformat.parse(data)["sections"]}


def values(section) -> dict:
    return {f.key: f.value for f in section.fields}


def stdlib_tar(fmt: int) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as t:
        info = tarfile.TarInfo("dir/hello.txt")
        info.size, info.mtime, info.uname, info.uid = 5, JAN_2024, "jane.doe", 1000
        t.addfile(info, io.BytesIO(b"hello"))
    return buf.getvalue()


# -- reading -------------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", [tarfile.USTAR_FORMAT, tarfile.GNU_FORMAT, tarfile.PAX_FORMAT])
def test_reads_what_tarfile_writes(fmt):
    archive = tarcore.open_tar(stdlib_tar(fmt))
    assert [e.name for e in archive.entries] == ["dir/hello.txt"]
    entry = archive.entries[0]
    assert archive.read(entry) == b"hello"
    assert entry.header.uname == b"jane.doe" and entry.header.mtime == JAN_2024
    assert archive.hidden == [] and archive.end_blocks == 2


def test_every_header_format_and_extension():
    long_name = "d/" + "x" * 150
    data = build_tar([
        folder("d"),
        file("d/a.txt", b"hello"),
        symlink("d/l", "a.txt"),
        hardlink("d/h", "d/a.txt"),
        pax({"path": long_name, "mtime": "1714559400.5"}),
        file("d/short", b"pax"),
        gnu_long("d/" + "y" * 120),
        gnu_long("target/" + "z" * 120, b"K"),
        symlink("d/yyy", "t", format=GNU),
        file("v7.txt", b"v7", format=V7),
        file("name", b"prefixed", prefix=b"some/prefix"),
        Header("dev/null", b"3", devmajor=1, devminor=3),
        Header("fifo", b"6"),
    ])
    archive = tarcore.open_tar(data)
    names = [e.name for e in archive.entries]
    assert names == ["d/", "d/a.txt", "d/l", "d/h", long_name, "d/" + "y" * 120, "v7.txt",
                     "some/prefix/name", "dev/null", "fifo"]
    assert archive.entries[5].linkname == "target/" + "z" * 120
    assert archive.entries[4].pax["mtime"] == b"1714559400.5"
    assert [e.header.format for e in archive.entries][5:7] == ["gnu", "v7"]
    assert archive.entries[8].header.devminor == 3
    assert archive.hidden == []


def test_base256_numbers():
    data = build_tar([file("a", b"x", raw_fields={"uid": base256(2_000_000_000, 8)})])
    assert tarcore.open_tar(data).entries[0].header.uid == 2_000_000_000


def test_global_pax_headers_apply_to_what_follows():
    data = build_tar([pax({"uname": "jane.doe", "comment": "nightly backup"}, flag=b"g"),
                      file("a", b"x"), file("b", b"y")])
    archive = tarcore.open_tar(data)
    assert len(archive.globals_) == 1
    assert archive.entries[1].globals_["uname"] == b"jane.doe"


def test_hidden_data_in_header_fields_padding_and_after_the_end():
    data = build_tar([
        file("a", b"x", padding=b"secret" + bytes(505)),
        file("b", b"y", patch={10: b"\x00hidden"}),
        file("c", b"z", patch={500: b"pad!"}),
        file("d", b"w", patch={270: b"\x00junk"}),
    ], suffix=b"after the end")
    hidden = tarcore.open_tar(data).hidden
    assert [h.where.split(" of the header")[0] for h in hidden] == [
        "in the padding after 'a'", "in the name field", "in the header padding",
        "in the uname field", "after the end of the archive"]


def test_gnu_unused_fields_are_hidden_data():
    data = build_tar([file("a", b"x", format=GNU, patch={400: b"sparse?"})])
    assert tarcore.open_tar(data).hidden[0].where.startswith("in the GNU sparse field")


def test_v7_unused_part_is_hidden_data():
    data = build_tar([file("a", b"x", format=V7, patch={300: b"hidden"})])
    assert "unused part" in tarcore.open_tar(data).hidden[0].where


def test_replaced_copies():
    data = build_tar([file("a.txt", b"old secret"), file("./a.txt", b"new")])
    archive = tarcore.open_tar(data)
    assert [archive.read(e) for e in archive.replaced()] == [b"old secret"]


def test_missing_or_short_end_is_a_warning():
    data = build_tar([file("a", b"x")], end_blocks=0, record=False)
    assert tarcore.open_tar(data).warnings == ["the TAR archive has no end-of-archive blocks"]
    data = build_tar([file("a", b"x")], end_blocks=1, record=False)
    assert "one zero block" in tarcore.open_tar(data).warnings[0]


@pytest.mark.parametrize("name", ["../evil", "a/../../evil", "/etc/passwd", "C:/x", "a\\b",
                                  "a\x01b"])
def test_unsafe_names_are_refused(name):
    with pytest.raises(MalformedFileError, match="refused"):
        tarcore.open_tar(build_tar([file(name, b"x")]))


def test_unsafe_pax_path_is_refused():
    with pytest.raises(MalformedFileError, match="traversal"):
        tarcore.open_tar(build_tar([pax({"path": "../evil"}), file("ok", b"x")]))


@pytest.mark.parametrize("headers, match", [
    ([file("a", b"x", typeflag=b"S")], "sparse"),
    ([pax({"GNU.sparse.major": "1"}), file("a", b"x")], "sparse"),
    ([file("a", b"x", typeflag=b"M")], "multi-volume"),
    ([file("a", b"x", typeflag=b"Q")], "unknown type"),
])
def test_unsupported_entries_are_refused(headers, match):
    with pytest.raises(UnsupportedFormatError, match=match):
        tarcore.open_tar(build_tar(headers))


def test_bad_checksum_is_refused():
    with pytest.raises(MalformedFileError, match="checksum"):
        tarcore.open_tar(build_tar([file("a", b"x", checksum=1234)]))


@pytest.mark.parametrize("cut", [100, 512, 513, 1000])
def test_truncated_archives_are_refused(cut):
    data = build_tar([file("a", b"x" * 600)])
    with pytest.raises(MalformedFileError, match="truncated|ends"):
        tarcore.open_tar(data[:cut])


@pytest.mark.parametrize("headers", [
    [file("a", b"x", raw_fields={"size": b"12345678901\x00"})],  # size past the end
    [file("a", b"x", raw_fields={"mode": b"0000z44\x00"})],
    [file("a", b"x", raw_fields={"size": b"\xff" * 12})],
    [Header(b"././@PaxHeader", b"x", data=b"99 path=a\n"), file("a", b"x")],
    [Header(b"././@PaxHeader", b"x", data=b"x path=a\n"), file("a", b"x")],
    [Header(b"././@PaxHeader", b"x", data=b"9 path\n"), file("a", b"x")],
    [Header(b"././@PaxHeader", b"x", data=b"x" * 600)],
    [folder("d", size=10)],
])
def test_malformed_headers_are_refused(headers):
    with pytest.raises(MalformedFileError):
        tarcore.open_tar(build_tar(headers))


def test_limits(monkeypatch):
    with pytest.raises(MalformedFileError, match="entries"):
        tarcore.open_tar(build_tar([file(f"f{i}", b"x") for i in range(5)]), max_entries=4)
    with pytest.raises(MalformedFileError, match="limit"):
        tarcore.open_tar(build_tar([file("a", b"x" * 2000)]), max_entry_size=1000)
    monkeypatch.setattr(tarcore, "MAX_EXTENDED_SIZE", 10)
    with pytest.raises(MalformedFileError, match="extended header"):
        tarcore.open_tar(build_tar([pax({"path": "a" * 50}), file("a", b"x")]))


def test_libarchive_xattrs_are_decoded():
    import base64

    value = base64.b64encode(b"0083;66320a80;Safari;X").rstrip(b"=")
    assert tarcore.xattr("LIBARCHIVE.xattr.com.apple.quarantine", value) == (
        "com.apple.quarantine", b"0083;66320a80;Safari;X")
    assert tarcore.xattr("LIBARCHIVE.xattr.com.apple.metadata%3AkMDItemWhereFroms",
                         b"YQ")[0] == "com.apple.metadata:kMDItemWhereFroms"
    assert tarcore.xattr("SCHILY.xattr.user.x", b"v") == ("user.x", b"v")
    assert tarcore.xattr("mtime", b"1") is None


# -- report ----------------------------------------------------------------------------------


def test_plain_archive_reveals_nothing():
    data = build_tar([folder("d"), file("d/a.txt", b"x"), file("run.sh", b"x", mode=0o755)])
    found = tarformat.parse(data)
    assert [s.key for s in found["sections"]] == ["archive_contents", "archive_files"]
    assert found["warnings"] == []


def test_kitchen_sink_report():
    data = build_tar([
        pax({"comment": "nightly backup", "uname": "jane.doe"}, flag=b"g"),
        folder("home/jane.doe", uname=b"jane.doe", gname=b"staff", uid=501, gid=20,
               mtime=JAN_2024),
        pax({"mtime": "1714559400.25", "atime": "1714559401", "ctime": "1714559402",
             "SCHILY.xattr.com.apple.quarantine": "0083;66320a80;Safari;X",
             "LIBARCHIVE.xattr.user.tag": "cmVk", "comment": "draft",
             "SCHILY.dev": "16777220"}),
        file("home/jane.doe/plan.txt", b"plan", mode=0o600, mtime=JAN_2024),
        file("home/jane.doe/._plan.txt", sample_appledouble()),
        file("home/jane.doe/.DS_Store", b"\x00\x00\x00\x01Bud1"),
        file("gnu.txt", b"x", format=GNU, atime=JAN_2024, ctime=JAN_2024),
        symlink("abs", "/home/jane.doe/secret"),
    ], suffix=b"after")
    found = sections(data)
    assert set(found) >= {"archive_contents", "archive_hidden", "archive_comments",
                          "archive_times", "archive_owners", "archive_system", "archive_junk",
                          "archive_xattrs", "archive_paths", "archive_links", "archive_extra"}
    owners = values(found["archive_owners"])
    assert "jane.doe/staff" in owners["owner_names"] and "501/20" in owners["owner_ids"]
    times = values(found["archive_times"])
    assert "accessed (pax) 2024-05-01T10:30:01+00:00" in times["times.home/jane.doe/plan.txt"]
    assert "accessed (GNU header)" in times["times.gnu.txt"]
    xattrs = {f.label: f.value for f in found["archive_xattrs"].fields}
    assert "downloaded with Safari" in xattrs["home/jane.doe/plan.txt: com.apple.quarantine"]
    assert xattrs["home/jane.doe/plan.txt: user.tag"] == "red"
    comments = values(found["archive_comments"])
    assert comments["comment.home/jane.doe/plan.txt"] == "draft"
    assert comments["comment.(global pax header 1)"] == "nightly backup"
    assert values(found["archive_system"])["permissions"] == "600 (file) (1 entry)"
    assert "SCHILY.dev" in found["archive_extra"].fields[0].label
    warnings = tarformat.parse(data)["warnings"]
    assert any("absolute path" in w for w in warnings)
    assert any(w.startswith("CRITICAL") and "hidden" in w for w in warnings)


def test_replaced_copies_are_hidden_data():
    data = build_tar([file("a.txt", b"old secret"), file("a.txt", b"new")])
    hidden = sections(data)["archive_hidden"]
    assert hidden.fields[0].value == b"old secret"


def test_volume_label_is_reported():
    data = build_tar([Header("Jane's backup 2024-05-01", b"V", format=GNU), file("a", b"x")])
    comments = values(sections(data)["archive_comments"])
    assert comments["comment.(volume label 1)"] == "Jane's backup 2024-05-01"


def test_structure():
    found = tarformat.parse(build_tar([file("a", b"x")]))
    assert found["structure"].get("end_blocks").value == 2
