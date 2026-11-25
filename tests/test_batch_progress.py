"""Progress lines during batch runs, --quiet, and unchanged single-file output."""

import json
import subprocess
import sys

import pytest

from helpers.jpeg import kitchen_sink_jpeg

JPEG = kitchen_sink_jpeg()


def run(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)], input="",
                          capture_output=True, text=True, encoding="utf-8", timeout=180)


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    (root / "a.jpg").write_bytes(JPEG)
    (root / "b.jpg").write_bytes(JPEG)
    (root / "c.jpg").write_bytes(JPEG[:300])
    return root


def test_strip_prints_one_numbered_line_per_file(folder):
    r = run("strip", folder)
    lines = [line for line in r.stdout.splitlines() if not line.startswith("  Warning: ")]
    assert lines[0] == f"[1/3] cleaned: {folder / 'a.jpg'} -> {folder / 'a_clean.jpg'}"
    assert lines[1] == f"[2/3] cleaned: {folder / 'b.jpg'} -> {folder / 'b_clean.jpg'}"
    assert lines[2].startswith(f"[3/3] refused: {folder / 'c.jpg'}: ")
    assert lines[3] == "" and lines[4] == "Summary: 3 entries"


def test_verify_prints_one_numbered_line_per_file(folder):
    r = run("verify", folder / "a.jpg", folder / "b.jpg")
    assert r.stdout.startswith(f"[1/2] not clean: {folder / 'a.jpg'}\n")
    assert f"\n[2/2] not clean: {folder / 'b.jpg'}\n" in r.stdout


@pytest.mark.parametrize("command", ["show", "risk"])
def test_report_headers_are_numbered(folder, command):
    r = run(command, folder)
    assert f"==> [1/3] {folder / 'a.jpg'}" in r.stdout.splitlines()
    assert f"==> [3/3] {folder / 'c.jpg'}" in r.stdout.splitlines()


@pytest.mark.parametrize("command", ["show", "risk", "strip", "verify"])
def test_quiet_prints_only_the_summary_and_failures(folder, command):
    missing = folder / "missing.jpg"
    r = run(command, folder, missing, "--quiet")
    assert r.returncode == 1
    assert r.stdout.startswith("\nSummary: 4 entries\n")
    assert "==>" not in r.stdout and "[1/3]" not in r.stdout
    assert f"metaex: error: {missing}: no such file or folder\n" in r.stderr
    if command == "strip":
        assert f"metaex: refused: {folder / 'c.jpg'}: " in r.stderr
    assert "[1/3]" not in r.stderr


@pytest.mark.parametrize("command", ["show", "risk", "strip", "verify"])
def test_json_keeps_stdout_clean_and_shows_progress_on_stderr(folder, command):
    r = run(command, folder, "--json")
    data = json.loads(r.stdout)
    assert len(data["files"]) == 3
    lines = r.stderr.splitlines()
    assert lines[0].startswith("[1/3] ") and lines[0].endswith(str(folder / "a.jpg"))
    assert len(lines) == 3
    quiet = run(command, folder, "--json", "--quiet")
    assert json.loads(quiet.stdout)["summary"] == data["summary"] or command == "strip"
    assert "[1/3]" not in quiet.stderr


def test_csv_progress_goes_to_stderr(folder):
    r = run("risk", folder, "--csv")
    assert r.stdout.startswith("﻿path,format,section,field,value,risk level\n")
    assert "[1/3] examined: " in r.stderr and "Summary: 3 entries" in r.stderr
    quiet = run("risk", folder, "--csv", "--quiet")
    assert "[1/3]" not in quiet.stderr and "Summary: 3 entries" in quiet.stderr
    assert quiet.stdout == r.stdout


@pytest.mark.parametrize("command, start", [("show", "metaex report: "),
                                            ("risk", "metaex risk: "),
                                            ("strip", "metaex strip: "),
                                            ("verify", "metaex verify: ")])
def test_single_file_output_is_unchanged(folder, command, start):
    r = run(command, folder / "a.jpg")
    assert r.stdout.startswith(start)
    assert "[1/1]" not in r.stdout + r.stderr and "Summary:" not in r.stdout
