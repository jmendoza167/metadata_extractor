"""Batch processing end to end, on a temporary copy of tests/samples (every
format, including the malformed samples): outcomes, summary counts, exit
codes, --output-dir mirroring, reruns, --overwrite, --in-place, permission
errors, Ctrl+C, CSV injection and the --json schema."""

import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from metaex import cli
from metaex.commands import strip as strip_command

SAMPLES = Path(__file__).parent / "samples"
ARCHIVES_WITH_METADATA_INSIDE = {"tar/kitchen_sink.tar", "tar/kitchen_sink.tar.gz",
                                 "tar/photo.jpg.gz", "zip/kitchen_sink.zip"}


def run(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)], cwd=cwd,
                          input="", capture_output=True, text=True, encoding="utf-8",
                          timeout=600)


def relative_files(root):
    out = []
    for folder, _, names in os.walk(root):
        out += [os.path.relpath(os.path.join(folder, n), root).replace(os.sep, "/")
                for n in names]
    return sorted(out)


def digest(root):
    return {rel: hashlib.sha256((Path(root) / rel).read_bytes()).hexdigest()
            for rel in relative_files(root)}


def by_relative(data, root):
    return {os.path.relpath(f["path"], root).replace(os.sep, "/"): f for f in data["files"]}


def kinds(root):
    """Sample paths by what metaex must do with them."""
    malformed, valid, other = set(), set(), set()
    for rel in relative_files(root):
        if rel.startswith("malformed/"):
            malformed.add(rel)
        elif rel == "SAMPLES.md" or rel.startswith("expected/"):
            other.add(rel)
        else:
            valid.add(rel)
    return malformed, valid, other


@pytest.fixture
def samples(tmp_path):
    root = tmp_path / "input"
    shutil.copytree(SAMPLES, root)
    shutil.rmtree(root / "real", ignore_errors=True)  # files from other software: not ours
    return root


@pytest.fixture(scope="module")
def stripped(tmp_path_factory):
    """One strip of all samples into an output folder, shared by several tests."""
    base = tmp_path_factory.mktemp("batch")
    root = base / "input"
    shutil.copytree(SAMPLES, root)
    shutil.rmtree(root / "real", ignore_errors=True)
    before = digest(root)
    r = run("strip", root, "--recursive", "--output-dir", base / "output", "--json")
    return root, base / "output", r, before


def test_every_sample_gets_the_right_outcome(stripped):
    root, _, r, _ = stripped
    assert r.returncode == 1, r.stderr  # the malformed samples are refused
    data = json.loads(r.stdout)
    files = by_relative(data, root)
    malformed, valid, other = kinds(root)
    assert set(files) == malformed | valid | other
    for rel in malformed:
        assert files[rel]["outcome"] == "refused", rel
        assert files[rel]["reason"].endswith("no file written"), rel
    for rel in valid:
        assert files[rel]["outcome"] in ("cleaned", "already clean"), (rel, files[rel])
        assert files[rel]["strip"]["verify"]["image_data_match"] is True, rel
    for rel in other:
        assert files[rel]["outcome"] == "skipped", rel
        assert files[rel]["reason"].startswith("unsupported format (detected type: ")
    assert files["jpeg/clean.jpg"]["outcome"] == "already clean"
    assert files["jpeg/kitchen_sink.jpg"]["outcome"] == "cleaned"


def test_summary_counts_match_the_files(stripped):
    root, _, r, _ = stripped
    data = json.loads(r.stdout)
    malformed, valid, other = kinds(root)
    s = data["summary"]
    assert s["files"] == len(malformed) + len(valid) + len(other)
    assert s["outcomes"]["refused"] == len(malformed)
    assert s["outcomes"]["cleaned"] + s["outcomes"]["already clean"] == len(valid)
    assert s["outcomes"]["skipped"] == len(other)
    assert s["outcomes"]["error"] == 0
    assert s["processed"] == s["to_process"] == s["files"] and s["interrupted"] is False
    assert sum(sum(c.values()) for c in s["formats"].values()) == s["files"]
    assert s["formats"]["JPEG"] == {"cleaned": 2, "already clean": 2, "refused": 1}


def test_output_dir_mirrors_the_valid_samples(stripped):
    root, output, _, before = stripped
    malformed, valid, _ = kinds(root)
    assert relative_files(output) == sorted(valid)
    assert digest(root) == before  # the originals are untouched
    assert not [p for p in relative_files(output) if ".metaex-" in p]


def test_verify_pairs_the_output_with_the_originals(stripped):
    root, output, _, _ = stripped
    r = run("verify", output, "--original", root, "--recursive", "--json")
    assert r.returncode == 3  # archives cleaned without --deep still hold metadata inside
    data = json.loads(r.stdout)
    cleaned = by_relative({"files": [f for f in data["files"]
                                     if f["path"].startswith(str(output))]}, output)
    malformed, valid, other = kinds(root)
    assert set(cleaned) == valid
    for rel, entry in cleaned.items():
        assert entry["original"] == str(root / Path(rel)), rel
        expected = "not clean" if rel in ARCHIVES_WITH_METADATA_INSIDE else "clean"
        assert entry["outcome"] == expected, (rel, entry["verify"]["leftovers"][:3])
        if expected == "not clean":
            assert "use --deep" in entry["verify"]["verdict"]
    originals = by_relative({"files": [f for f in data["files"]
                                       if f["path"].startswith(str(root))]}, root)
    assert set(originals) == malformed | other
    assert {f["reason"] for f in originals.values()} == {
        "original without a cleaned copy (not checked)"}


def test_deep_cleans_everything(samples, tmp_path):
    out = tmp_path / "deep"
    r = run("strip", samples, "--recursive", "--deep", "--output-dir", out, "--quiet")
    assert r.returncode == 1  # still the malformed samples
    v = run("verify", out, "--original", samples, "--recursive", "--json")
    assert v.returncode == 0, [f["path"] for f in json.loads(v.stdout)["files"]
                               if f["outcome"] == "not clean"]


def test_next_to_originals_and_reruns(samples):
    malformed, valid, other = kinds(samples)
    first = run("strip", samples, "--recursive", "--json")
    assert first.returncode == 1
    after_first = relative_files(samples)
    clean_copies = {rel for rel in after_first if rel not in malformed | valid | other}
    assert len(clean_copies) == len(valid)
    assert all("_clean." in rel for rel in clean_copies)

    # A rerun cleans nothing again: outputs exist, and *_clean.* files are left out.
    second = run("strip", samples, "--recursive", "--json")
    assert second.returncode == 1
    files = by_relative(json.loads(second.stdout), samples)
    for rel in valid:
        assert files[rel]["outcome"] == "skipped"
        assert files[rel]["reason"].startswith("output already exists: ")
    for rel in clean_copies:
        assert files[rel] == {**files[rel], "outcome": "skipped",
                              "reason": "metaex output (*_clean.*)"}
    assert relative_files(samples) == after_first
    assert not [rel for rel in after_first if "_clean_clean" in rel]

    # --overwrite replaces the cleaned copies (and still never cleans them).
    third = run("strip", samples, "--recursive", "--overwrite", "--json")
    files = by_relative(json.loads(third.stdout), samples)
    for rel in valid:
        assert files[rel]["outcome"] in ("cleaned", "already clean"), rel
    assert relative_files(samples) == after_first


def test_in_place_with_yes(samples):
    malformed, valid, other = kinds(samples)
    before = digest(samples)
    r = run("strip", samples, "--recursive", "--in-place", "--yes", "--quiet")
    assert r.returncode == 1
    after = digest(samples)
    assert set(after) == set(before)  # no new files, nothing removed
    for rel in malformed | other:
        assert after[rel] == before[rel], rel
    assert sum(after[rel] != before[rel] for rel in valid) >= 20
    v = run("verify", samples, "--recursive", "--json")
    files = by_relative(json.loads(v.stdout), samples)
    for rel in valid - ARCHIVES_WITH_METADATA_INSIDE:
        assert files[rel]["outcome"] == "clean", rel


class Terminal(io.StringIO):
    def isatty(self):
        return True


def test_in_place_confirmation_refused_changes_nothing(samples, monkeypatch, capsys):
    before = digest(samples)
    monkeypatch.setattr(sys, "stdin", Terminal("no\n"))
    assert cli.main(["strip", str(samples), "--recursive", "--in-place"]) == 1
    err = capsys.readouterr().err
    malformed, valid, other = kinds(samples)
    total = len(malformed) + len(valid) + len(other)
    assert f"Type 'yes' to overwrite {total} files: " in err
    assert digest(samples) == before


@pytest.mark.skipif(sys.platform == "win32" or getattr(os, "geteuid", lambda: 0)() == 0,
                    reason="needs POSIX permissions and a user other than root")
def test_permission_errors_do_not_stop_the_batch(samples, tmp_path):
    locked = samples / "jpeg" / "gps_camera.jpg"
    locked.chmod(0)
    try:
        r = run("strip", samples / "jpeg", "--output-dir", tmp_path / "out", "--json")
    finally:
        locked.chmod(0o644)
    assert r.returncode == 1
    files = by_relative(json.loads(r.stdout), samples / "jpeg")
    assert files["gps_camera.jpg"]["outcome"] == "error"
    assert "permission denied" in files["gps_camera.jpg"]["reason"]
    assert files["kitchen_sink.jpg"]["outcome"] == "cleaned"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file permissions (icacls)")
def test_permission_errors_do_not_stop_the_batch_on_windows(samples, tmp_path):
    locked = samples / "jpeg" / "gps_camera.jpg"
    user = os.environ.get("USERNAME", "")
    deny = subprocess.run(["icacls", str(locked), "/deny", f"{user}:(R)"],
                          capture_output=True, text=True)
    if deny.returncode != 0 or not user:
        pytest.skip(f"cannot change permissions here: {deny.stderr.strip()}")
    try:
        r = run("strip", samples / "jpeg", "--output-dir", tmp_path / "out", "--json")
    finally:
        subprocess.run(["icacls", str(locked), "/remove:d", user], capture_output=True)
    assert r.returncode == 1
    files = by_relative(json.loads(r.stdout), samples / "jpeg")
    assert files["gps_camera.jpg"]["outcome"] == "error"
    assert "permission denied" in files["gps_camera.jpg"]["reason"]
    assert files["kitchen_sink.jpg"]["outcome"] == "cleaned"


def test_unwritable_output_folder_is_an_error_per_file(samples, tmp_path):
    blocker = tmp_path / "out"
    blocker.mkdir()
    (blocker / "jpeg").write_bytes(b"a file where a folder is needed")
    r = run("strip", samples, "--recursive", "--output-dir", blocker, "--json")
    files = by_relative(json.loads(r.stdout), samples)
    for rel in ("jpeg/clean.jpg", "jpeg/kitchen_sink.jpg"):
        assert files[rel]["outcome"] in ("refused", "error"), files[rel]
    assert files["png/clean.png"]["outcome"] in ("cleaned", "already clean")


def test_ctrl_c_stops_cleanly_with_a_partial_summary(samples, tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    real_verify = strip_command.verify
    checks = []

    def interrupting_verify(data, original=None, filename=None):
        # strip's check of each new temp file passes the output path
        if filename is not None and str(filename).startswith(str(out)):
            checks.append(filename)
            if len(checks) == 5:
                raise KeyboardInterrupt
        return real_verify(data, original, filename=filename)

    monkeypatch.setattr(strip_command, "verify", interrupting_verify)
    code = cli.main(["strip", str(samples / "jpeg"), str(samples / "png"), "--output-dir",
                     str(out)])
    captured = capsys.readouterr()
    assert code == 1
    assert "metaex: error: interrupted" in captured.err
    assert "INTERRUPTED: stopped after 4 of 8 file(s); this summary is partial" in captured.out
    assert "\nSummary: " in captured.out
    written = relative_files(out)
    assert len(written) == 4
    assert not [p for p in written if ".metaex-" in p or p.endswith(".tmp")]
    leftovers = [p for p in relative_files(tmp_path) if ".metaex-" in p]
    assert leftovers == []


def test_csv_of_all_samples_is_safe_to_open(samples):
    r = subprocess.run([sys.executable, "-m", "metaex", "risk", str(samples), "--recursive",
                        "--csv"], capture_output=True, timeout=600, input=b"")
    assert r.returncode == 0
    assert r.stdout.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(r.stdout.decode("utf-8")[1:], newline="")))
    assert rows[0] == ["path", "format", "section", "field", "value", "risk level"]
    assert len(rows) > 500
    for row in rows[1:]:
        assert len(row) == 6
        for cell in row:
            assert not cell.startswith(("=", "+", "-", "@", "\t", "\r")), row
    assert {row[5] for row in rows[1:]} == {"critical", "high", "medium", "low"}
    assert "Summary: " in r.stderr.decode("utf-8")


@pytest.mark.parametrize("command, extra", [
    ("show", "report"), ("risk", "risk"), ("verify", "verify")])
def test_json_schema(samples, command, extra):
    r = run(command, samples, "--recursive", "--json")
    data = json.loads(r.stdout)
    assert set(data) == {"command", "interrupted", "files", "summary"}
    assert data["command"] == command and data["interrupted"] is False
    summary_keys = {"files", "outcomes", "formats", "interrupted", "to_process", "processed"}
    assert set(data["summary"]) == summary_keys | ({"risk"} if command == "risk" else set())
    for entry in data["files"]:
        base = {"path", "outcome", "format", "reason"}
        if entry["outcome"] in ("skipped", "refused", "error"):
            assert set(entry) == base, entry
        else:
            expected = base | {extra} | ({"original"} if command == "verify" else set())
            assert set(entry) == expected, entry
            assert isinstance(entry[extra], dict)


def test_text_summary_matches_json(stripped):
    root, _, r, _ = stripped
    data = json.loads(r.stdout)["summary"]
    text = run("risk", root, "--recursive", "--quiet").stdout
    assert text.startswith(f"\nSummary: {data['files']} entries\n")
