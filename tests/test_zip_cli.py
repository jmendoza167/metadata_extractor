"""metaex show and risk on plain ZIP archives, through the command line."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from helpers.appledouble import sample_appledouble
from helpers.zip import Entry, build_zip, dos_stamp, make_zip, unix_ids, unix_times
from metaex.core import filetype as ft


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


def kitchen_sink() -> bytes:
    date, time = dos_stamp(2024, 5, 1, 10, 30)
    return build_zip([
        Entry("plans/", date=date, time=time),
        Entry("plans/floor.txt", b"Kitchen and dining", date=date, time=time,
              local_extra=unix_times(1714559400) + unix_ids(1000, 1000),
              comment=b"draft by jane.doe@example.com"),
        Entry("__MACOSX/plans/._floor.txt", sample_appledouble()),
        Entry("plans/.DS_Store", b"\x00\x00\x00\x01Bud1"),
        Entry("home/jane.doe/todo.txt", b"call the landlord"),
    ], comment=b"Backup of Jane's laptop", suffix=b"appended")


def test_show_json(tmp_path):
    path = tmp_path / "plans.zip"
    path.write_bytes(kitchen_sink())
    r = metaex("show", "--json", "--structure", path)
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    sections = report["sections"]
    assert sections["file_type"]["fields"]["name"]["value"] == "ZIP"
    assert sections["file_type"]["fields"]["embedded_metadata"]["value"] == "examined"
    assert sections["archive_contents"]["fields"]["entries"]["value"] == 5
    for key in ("archive_hidden", "archive_comments", "archive_times", "archive_owners",
                "archive_junk", "archive_xattrs", "archive_paths", "structure"):
        assert key in sections, key
    assert any(w.startswith("CRITICAL") for w in report["warnings"])


def test_show_text(tmp_path):
    path = tmp_path / "plans.zip"
    path.write_bytes(kitchen_sink())
    r = metaex("show", path)
    assert r.returncode == 0, r.stderr
    for text in ("Operating system files", "Extended attributes", "downloaded with Safari",
                 "Backup of Jane's laptop", "user folder of 'jane.doe'"):
        assert text in r.stdout, text


def test_risk(tmp_path):
    path = tmp_path / "plans.zip"
    path.write_bytes(kitchen_sink())
    r = metaex("risk", "--json", path)
    assert r.returncode == 0, r.stderr
    found = json.loads(r.stdout)
    summary = found["summary"]
    assert summary["critical"] >= 5 and summary["high"] >= 3 and summary["medium"] >= 1
    assert {f["section"] for f in found["findings"] if f["level"] == "critical"} >= {
        "Hidden data in the archive", "Extended attributes", "Names that reveal a user folder"}


def test_clean_archive_has_no_findings(tmp_path):
    path = tmp_path / "clean.zip"
    path.write_bytes(make_zip({"a.txt": "hello", "b/c.txt": "world"}))
    r = metaex("risk", path)
    assert r.returncode == 0, r.stderr
    assert "0 critical, 0 high, 0 medium, 0 low" in r.stdout


def test_malformed_archive_is_reported_not_examined(tmp_path):
    path = tmp_path / "evil.zip"
    path.write_bytes(build_zip([Entry("../evil.txt", b"x")]))
    r = metaex("show", "--json", path)
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report["sections"]["file_type"]["fields"]["embedded_metadata"]["value"] == \
        "not examined (file is malformed)"
    assert any("path traversal" in w for w in report["warnings"])


def test_zip_with_data_in_front_is_detected(tmp_path):
    data = b"#!/bin/sh\necho self-extracting\n" + build_zip([Entry("a.txt", b"x")])
    detected = ft.detect(data)
    assert detected.key == ft.ZIP.key and "data before the start" in detected.warnings[0]
    path = tmp_path / "script.zip"
    path.write_bytes(data)
    r = metaex("risk", "--json", path)
    found = json.loads(r.stdout)
    assert [f["label"] for f in found["findings"]] == ["Data before the first entry"]


@pytest.mark.parametrize("files, expected", [
    ({"META-INF/MANIFEST.MF": "Manifest-Version: 1.0\n", "a/B.class": "x"}, ft.JAR),
    ({"WEB-INF/web.xml": "<web-app/>"}, ft.JAR),
    ({"AndroidManifest.xml": "x", "classes.dex": "x"}, ft.APK),
    ({"mimetype": "application/epub+zip", "META-INF/container.xml": "<c/>"}, ft.EPUB),
])
def test_zip_based_formats_are_refused_clearly(tmp_path, files, expected):
    data = make_zip(files)
    assert ft.detect(data) == expected
    path = tmp_path / f"file{expected.extensions[0]}"
    path.write_bytes(data)
    r = metaex("show", path)
    assert r.returncode == 0
    assert ft.refusal(expected).split(":")[0] in " ".join(r.stdout.split())
    r = metaex("strip", path)
    assert r.returncode == 1 and "no file written" in r.stderr
    assert not list(tmp_path.glob("*_clean*"))


def test_strip_is_available(tmp_path):
    path = tmp_path / "plans.zip"
    path.write_bytes(kitchen_sink())
    r = metaex("show", "--json", path)
    support = json.loads(r.stdout)["sections"]["file_type"]["fields"]["support"]["value"]
    assert support == "supported"
