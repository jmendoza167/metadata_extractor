"""End to end on the command line: show -> strip -> verify -> show for DOCX,
XLSX and PPTX, with the strip options and the refusals."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest

from helpers.ooxml import (DECL, kitchen_sink_docx, kitchen_sink_pptx, kitchen_sink_xlsx,
                           macro_docm)
from helpers.zip import Entry, build_zip
from metaex.core.ziparchive import open_archive

NOT_METADATA = {"file_type", "file_system", "hashes"}
KEPT = {"office_comments", "office_people", "tracked_changes", "hidden_content",
        "office_external", "xlsx_external_data", "office_embedded", "macros", "embedded_images",
        "office_custom_xml"}
"""Sections a cleaned file may still have (with anonymous or content values)."""
FILES = {"letter.docx": kitchen_sink_docx, "budget.xlsx": kitchen_sink_xlsx,
         "talk.pptx": kitchen_sink_pptx, "macro.docm": macro_docm}


def metaex(*args, stdin=None):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", timeout=120,
                          input=stdin)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def show(path) -> dict:
    r = metaex("show", path, "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("name", sorted(FILES))
@pytest.mark.parametrize("options", [[], ["--paranoid"], ["--remove-comments"],
                                     ["--paranoid", "--remove-comments", "--no-orientation"]])
def test_show_strip_verify_show(name, options, tmp_path):
    original = tmp_path / name
    original.write_bytes(FILES[name]())
    before = sha(original)
    shown = show(original)
    assert shown["sections"]["office_core"]["fields"]["creator"]["value"] == "Jane Doe"

    r = metaex("strip", original, *options)
    assert r.returncode == 0, r.stderr
    cleaned = tmp_path / name.replace(".", "_clean.")
    assert "Verification: CLEAN" in r.stdout
    assert sha(original) == before  # the original is never touched

    v = metaex("verify", cleaned, "--original", original, "--json")
    assert v.returncode == 0, v.stdout
    result = json.loads(v.stdout)
    assert result["clean"] and result["image_data_match"]

    after = show(cleaned)
    assert set(after["sections"]) - NOT_METADATA <= KEPT
    # Hidden text, the glossary and data connections are content: kept (with a
    # warning), so the made-up name in them may stay.
    content = ("hidden_content", "xlsx_external_data")
    text = json.dumps({k: v for k, v in after["sections"].items() if k not in content})
    for secret in ("Jane Doe", "jane.doe", "John Example", "Example Corp", "2024-05-01T10:00",
                   "MSIP_Label", "HP LaserJet", "GPS"):
        assert secret not in text, secret
    if "--remove-comments" in options:
        assert "office_comments" not in after["sections"]
        # people.xml stays (anonymous) only while tracked changes refer to it
        people = after["sections"].get("office_people", {"fields": {}})["fields"]
        assert {f["value"] for f in people.values()} <= {"Author"}


def test_strip_summary_shows_the_tracked_changes_warning_first(tmp_path):
    original = tmp_path / "letter.docx"
    original.write_bytes(kitchen_sink_docx())
    r = metaex("strip", original)
    lines = r.stdout.splitlines()
    assert lines[3] == "*** WARNING ***" and "DELETED TEXT" in lines[4]
    assert "Accept All" in lines[4]
    assert "Removed:" in r.stdout and "Document properties" in r.stdout
    assert 'kept 1 comment(s) (their authors are now "Author")' in r.stdout


def test_strip_json_lists_warnings_and_verification(tmp_path):
    original = tmp_path / "budget.xlsx"
    original.write_bytes(kitchen_sink_xlsx())
    outcome = json.loads(metaex("strip", original, "--json").stdout)
    assert outcome["type"] == "XLSX" and outcome["verify"]["clean"]
    assert any("hidden sheet(s)" in w for w in outcome["warnings"])
    assert "Workbook history" in outcome["removed"]


@pytest.mark.parametrize("name", sorted(FILES))
def test_in_place(name, tmp_path):
    path = tmp_path / name
    path.write_bytes(FILES[name]())
    refused = metaex("strip", path, "--in-place", stdin="")
    assert refused.returncode == 1 and "--yes" in refused.stderr
    r = metaex("strip", path, "--in-place", "--yes")
    assert r.returncode == 0, r.stderr
    assert os.listdir(tmp_path) == [name]
    assert metaex("verify", path).returncode == 0
    assert "office_core" not in show(path)["sections"]


def test_existing_output_is_left_alone(tmp_path):
    original = tmp_path / "letter.docx"
    original.write_bytes(kitchen_sink_docx())
    existing = tmp_path / "letter_clean.docx"
    existing.write_bytes(b"do not touch")
    r = metaex("strip", original)
    assert r.returncode == 1 and "already exists" in r.stderr
    assert existing.read_bytes() == b"do not touch"


def test_rename_uses_the_canonical_extension(tmp_path):
    original = tmp_path / "Jane's letter.docx"
    original.write_bytes(kitchen_sink_docx())
    r = metaex("strip", original, "--rename")
    assert r.returncode == 0, r.stderr
    new = [p for p in os.listdir(tmp_path) if p.startswith("img_")]
    assert len(new) == 1 and new[0].endswith(".docx")


def test_verify_exit_codes(tmp_path):
    original = tmp_path / "talk.pptx"
    original.write_bytes(kitchen_sink_pptx())
    assert metaex("verify", original).returncode == 3  # not clean
    metaex("strip", original)
    assert metaex("verify", tmp_path / "talk_clean.pptx").returncode == 0


def bad_files() -> dict[str, tuple[bytes, str]]:
    kitchen = kitchen_sink_xlsx()
    archive = open_archive(kitchen)
    files = {n: archive.read(n) for n in archive.names()}
    doctype = dict(files)
    doctype["xl/styles.xml"] = (DECL + '<!DOCTYPE s [<!ENTITY x "y">]><styleSheet/>').encode()
    return {
        "truncated.xlsx": (kitchen[: len(kitchen) * 2 // 3], "the file may be truncated"),
        "appended.pptx": (kitchen_sink_pptx()[:-22], "the file may be truncated"),
        "doctype.xlsx": (build_zip([Entry(n, d) for n, d in doctype.items()]), "DOCTYPE"),
        "crc.docx": (kitchen_sink_docx().replace(b"Kitchen", b"Kitchem", 1), "bad CRC-32"),
    }


@pytest.mark.parametrize("name", sorted(bad_files()))
def test_damaged_files_are_refused_and_nothing_is_written(name, tmp_path):
    data, message = bad_files()[name]
    path = tmp_path / name
    path.write_bytes(data)
    for args in ([path], [path, "--paranoid"], [path, "--remove-comments"], [path, "--rename"]):
        r = metaex("strip", *args)
        assert r.returncode == 1, (args, r.stdout)
        assert message in r.stderr and "no file written" in r.stderr
        assert "internal error" not in r.stderr and "Traceback" not in r.stderr
        assert os.listdir(tmp_path) == [name]
    r = metaex("strip", path, "--in-place", "--yes")
    assert r.returncode == 1 and path.read_bytes() == data
    assert metaex("show", path).returncode == 0
    assert metaex("risk", path).returncode == 0
