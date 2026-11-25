"""The summary after a batch run: counts per outcome and per format, the
refused, failed and skipped files with their reasons, --json, exit codes."""

import json
import os
import subprocess
import sys

import pytest

from helpers.jpeg import kitchen_sink_jpeg
from metaex.commands import batch
from metaex.commands.batch import BatchRun, FileResult, summarize, summary_lines

JPEG = kitchen_sink_jpeg()


def run(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)], input="",
                          capture_output=True, text=True, encoding="utf-8", timeout=180)


def sample_run():
    return BatchRun(results=[
        FileResult("a.jpg", batch.CLEANED, "JPEG"),
        FileResult("b.jpg", batch.ALREADY_CLEAN, "JPEG"),
        FileResult("c.png", batch.REFUSED, "PNG", reason="c.png: bad CRC; no file written"),
        FileResult("d.txt", batch.SKIPPED, "unknown",
                   reason="unsupported format (detected type: unknown)"),
        FileResult(".hidden", batch.SKIPPED, reason="hidden (use --include-hidden to include it)"),
        FileResult("gone.jpg", batch.ERROR, reason="no such file or folder"),
    ], total=4, processed=4)


def test_summarize_counts():
    s = summarize("strip", sample_run())
    assert s["files"] == 6
    assert s["outcomes"] == {"cleaned": 1, "already clean": 1, "skipped": 2, "refused": 1,
                             "error": 1}
    assert s["formats"] == {"JPEG": {"cleaned": 1, "already clean": 1},
                            "PNG": {"refused": 1},
                            "unknown": {"skipped": 1},
                            "(not read)": {"skipped": 1, "error": 1}}
    assert s["interrupted"] is False and s["to_process"] == 4 and s["processed"] == 4


@pytest.mark.parametrize("command, outcomes", [
    ("show", ["examined", "skipped", "refused", "error"]),
    ("risk", ["examined", "skipped", "refused", "error"]),
    ("strip", ["cleaned", "already clean", "skipped", "refused", "error"]),
    ("verify", ["clean", "not clean", "skipped", "refused", "error"]),
])
def test_every_outcome_of_the_command_is_counted_even_zero(command, outcomes):
    assert list(summarize(command, BatchRun())["outcomes"]) == outcomes


def test_summary_text():
    lines = summary_lines("strip", sample_run())
    text = "\n".join(lines)
    assert lines[1] == "Summary: 6 entries"
    assert "  cleaned        1" in lines and "  refused        1" in lines
    assert "  JPEG        2 (1 cleaned, 1 already clean)" in lines
    assert "  (not read)  2 (1 skipped, 1 error)" in lines
    # refused, then errors, then skipped; reasons without the repeated path
    assert text.index("Refused (1):") < text.index("Error (1):") < text.index("Skipped (2):")
    assert "  c.png: bad CRC; no file written" in lines
    assert "  gone.jpg: no such file or folder" in lines
    assert "  .hidden: hidden (use --include-hidden to include it)" in lines
    assert lines[-1] == 'Run "metaex formats" to see the supported formats.'


def test_partial_summary_after_ctrl_c():
    r = sample_run()
    r.interrupted, r.total, r.processed = True, 10, 4
    lines = summary_lines("strip", r)
    assert "INTERRUPTED: stopped after 4 of 10 file(s); this summary is partial" in lines


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "a.jpg").write_bytes(JPEG)
    (tmp_path / "broken.jpg").write_bytes(JPEG[:300])
    (tmp_path / "notes.txt").write_bytes(b"text")
    (tmp_path / ".hidden.jpg").write_bytes(JPEG)
    return tmp_path


def test_summary_after_strip(folder):
    r = run("strip", folder)
    assert r.returncode == 1
    out = r.stdout
    assert "\nSummary: 4 entries\n" in out
    assert out.index("cleaned: ") < out.index("Summary: ")
    assert f"  {folder / 'broken.jpg'}: APP1 segment at offset 110 is truncated" in out
    assert f"  {folder / '.hidden.jpg'}: hidden (use --include-hidden to include it)" in out
    assert f"  {folder / 'notes.txt'}: unsupported format (detected type: unknown)" in out


@pytest.mark.parametrize("command", ["show", "risk", "strip", "verify"])
def test_json_has_every_file_and_the_summary(folder, command):
    r = run(command, folder, "--json")
    data = json.loads(r.stdout)
    assert set(data) == {"command", "interrupted", "files", "summary"}
    assert data["summary"]["files"] == len(data["files"]) == 4
    paths = [f["path"] for f in data["files"]]
    assert paths == sorted(paths, key=batch.sort_key)
    for entry in data["files"]:
        assert {"path", "outcome", "format", "reason"} <= set(entry)
        assert entry["outcome"] in batch.COMMAND_OUTCOMES[command]
    assert sum(data["summary"]["outcomes"].values()) == 4


@pytest.mark.parametrize("command, files, code", [
    ("strip", {"a.jpg": JPEG}, 0),
    ("strip", {"a.jpg": JPEG, "b.jpg": JPEG[:300]}, 1),
    ("verify", {"a.jpg": JPEG}, 3),
    ("verify", {"a.jpg": JPEG, "b.txt": b"x"}, 3),
    ("show", {"a.jpg": JPEG, "b.txt": b"x"}, 0),
    ("risk", {"b.txt": b"x"}, 0),
])
def test_exit_codes(tmp_path, command, files, code):
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    assert run(command, tmp_path).returncode == code


def test_exit_code_1_wins_over_3(tmp_path):
    (tmp_path / "a.jpg").write_bytes(JPEG)
    r = run("verify", tmp_path, tmp_path / "missing.jpg")
    assert r.returncode == 1


def test_risk_summary_counts_the_findings(tmp_path):
    (tmp_path / "a.jpg").write_bytes(JPEG)
    (tmp_path / "b.jpg").write_bytes(JPEG)
    (tmp_path / "notes.txt").write_bytes(b"x")
    data = json.loads(run("risk", tmp_path, "--json").stdout)
    single = json.loads(run("risk", tmp_path / "a.jpg", "--json").stdout)["summary"]
    totals = data["summary"]["risk"]
    assert list(totals) == ["critical", "high", "medium", "low"]
    for level, n in single.items():
        assert totals[level] == {"findings": 2 * n, "files": 2 if n else 0}
    out = run("risk", tmp_path).stdout
    summary = out[out.index("\nSummary: "):]
    assert f"  critical  {2 * single['critical']} (in 2 files)" in summary
    assert f"Files with critical findings (2):\n  {tmp_path / 'a.jpg'}: " in summary


def test_single_file_has_no_summary(tmp_path):
    (tmp_path / "a.jpg").write_bytes(JPEG)
    r = run("strip", tmp_path / "a.jpg")
    assert "Summary:" not in r.stdout
    assert os.path.exists(tmp_path / "a_clean.jpg")
