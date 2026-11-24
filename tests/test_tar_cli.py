"""metaex show and risk on TAR archives, through the command line."""

from __future__ import annotations

import json
import subprocess
import sys

from helpers.appledouble import sample_appledouble
from helpers.tar import GNU, JAN_2024, V7, build_tar, file, folder, pax, symlink
from metaex.core import filetype as ft


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


def kitchen_sink() -> bytes:
    return build_tar([
        pax({"comment": "nightly backup of jane.doe@example.com"}, flag=b"g"),
        folder("home/jane.doe", uname=b"jane.doe", gname=b"staff", uid=501, gid=20,
               mtime=JAN_2024),
        pax({"SCHILY.xattr.com.apple.quarantine": "0083;66320a80;Safari;X"}),
        file("home/jane.doe/plan.txt", b"plan", mode=0o600, mtime=JAN_2024),
        file("home/jane.doe/._plan.txt", sample_appledouble()),
        file("gnu.txt", b"x", format=GNU, atime=JAN_2024),
        symlink("rel", "home/jane.doe/plan.txt"),
    ], suffix=b"after the end")


def test_v7_and_gnu_archives_are_detected():
    assert ft.detect(build_tar([file("a.txt", b"x", format=V7)])) == ft.TAR
    assert ft.detect(build_tar([file("a.txt", b"x", format=GNU)])) == ft.TAR
    assert ft.detect(b"a.txt" + bytes(507)) != ft.TAR


def test_show_json(tmp_path):
    path = tmp_path / "backup.tar"
    path.write_bytes(kitchen_sink())
    r = metaex("show", "--json", "--structure", path)
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    sections = report["sections"]
    assert sections["file_type"]["fields"]["embedded_metadata"]["value"] == "examined"
    for key in ("archive_contents", "archive_hidden", "archive_comments", "archive_times",
                "archive_owners", "archive_junk", "archive_xattrs", "archive_paths",
                "archive_links", "structure"):
        assert key in sections, key


def test_risk(tmp_path):
    path = tmp_path / "backup.tar"
    path.write_bytes(kitchen_sink())
    r = metaex("risk", "--json", path)
    assert r.returncode == 0, r.stderr
    found = json.loads(r.stdout)
    critical = {(f["section"], f["label"]) for f in found["findings"] if f["level"] == "critical"}
    assert ("Archive comments", "Comment on (global pax header 1)") in critical  # e-mail
    assert ("Extended attributes", "home/jane.doe/plan.txt: com.apple.quarantine") in critical
    assert ("Names that reveal a user folder", "home/jane.doe") in critical
    assert ("Hidden data in the archive", "Data after the end of the archive") in critical
    high = {f["section"] for f in found["findings"] if f["level"] == "high"}
    assert {"Entry times", "Entry owners", "Operating system files"} <= high


def test_clean_archive_has_no_findings(tmp_path):
    path = tmp_path / "clean.tar"
    path.write_bytes(build_tar([folder("d"), file("d/a.txt", b"x")]))
    r = metaex("risk", path)
    assert r.returncode == 0, r.stderr
    assert "0 critical, 0 high, 0 medium, 0 low" in r.stdout


def test_malformed_archive_is_reported(tmp_path):
    path = tmp_path / "evil.tar"
    path.write_bytes(build_tar([file("../../evil", b"x")]))
    r = metaex("show", "--json", path)
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert any("path traversal" in w for w in report["warnings"])
