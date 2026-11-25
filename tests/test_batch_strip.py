"""strip on many files: where outputs go, existing outputs, --overwrite,
--in-place confirmation, per-file options, and the safety checks."""

import io
import json
import os
import subprocess
import sys

import pytest

from helpers.jpeg import kitchen_sink_jpeg
from helpers.tar import build_tar, file as tar_file
from metaex import cli
from metaex.core.verify import verify

JPEG = kitchen_sink_jpeg()


def run(*args, cwd=None):
    # input="": stdin is an empty pipe, never a terminal
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", timeout=180,
                          input="")


def files(root):
    out = []
    for folder, _, names in os.walk(root):
        for name in names:
            out.append(os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/"))
    return sorted(out)


def outcomes(result):
    data = json.loads(result.stdout)
    return {os.path.basename(f["path"]): f["outcome"] for f in data["files"]}


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "in"
    (root / "sub").mkdir(parents=True)
    (root / "a.jpg").write_bytes(JPEG)
    (root / "notes.txt").write_bytes(b"plain text\n")
    (root / "sub" / "b.jpg").write_bytes(JPEG)
    return root


def test_cleaned_copies_go_next_to_the_originals(folder):
    r = run("strip", folder, "--recursive")
    assert r.returncode == 0, r.stdout + r.stderr
    assert files(folder) == ["a.jpg", "a_clean.jpg", "notes.txt", "sub/b.jpg",
                             "sub/b_clean.jpg"]
    assert verify((folder / "a_clean.jpg").read_bytes()).clean
    assert (folder / "a.jpg").read_bytes() == JPEG
    lines = r.stdout.splitlines()
    assert f"cleaned: {folder / 'a.jpg'} -> {folder / 'a_clean.jpg'}" in lines
    assert (f"skipped: {folder / 'notes.txt'}: unsupported format (detected type: unknown)"
            in lines)


def test_rerun_skips_existing_outputs_and_never_cleans_clean_copies(folder):
    assert run("strip", folder, "--recursive").returncode == 0
    r = run("strip", folder, "--recursive", "--json")
    assert r.returncode == 0
    assert outcomes(r) == {"a.jpg": "skipped", "a_clean.jpg": "skipped", "notes.txt": "skipped",
                           "b.jpg": "skipped", "b_clean.jpg": "skipped"}
    reasons = {os.path.basename(f["path"]): f["reason"] for f in json.loads(r.stdout)["files"]}
    assert reasons["a.jpg"].startswith("output already exists: ")
    assert reasons["a.jpg"].endswith("(use --overwrite to replace it)")
    assert reasons["a_clean.jpg"] == "metaex output (*_clean.*)"
    assert "a_clean_clean.jpg" not in files(folder)


def test_overwrite_replaces_existing_outputs(folder):
    (folder / "a_clean.jpg").write_bytes(b"old")
    r = run("strip", folder, "--overwrite", "--json")
    assert r.returncode == 0
    assert outcomes(r)["a.jpg"] == "cleaned"
    assert verify((folder / "a_clean.jpg").read_bytes()).clean


def test_output_dir_mirrors_the_folders_and_keeps_names(folder, tmp_path):
    out = tmp_path / "out"
    r = run("strip", folder, "--recursive", "--output-dir", out)
    assert r.returncode == 0, r.stdout + r.stderr
    assert files(out) == ["a.jpg", "sub/b.jpg"]
    assert files(folder) == ["a.jpg", "notes.txt", "sub/b.jpg"]
    assert verify((out / "sub" / "b.jpg").read_bytes()).clean


def test_output_dir_inside_the_input_folder_is_left_out(folder):
    out = folder / "cleaned"
    assert run("strip", folder, "--recursive", "--output-dir", out).returncode == 0
    r = run("strip", folder, "--recursive", "--output-dir", out, "--overwrite")
    assert r.returncode == 0
    assert files(out) == ["a.jpg", "sub/b.jpg"]


def test_output_dir_that_is_an_input_folder_is_refused(folder):
    r = run("strip", folder, "--output-dir", folder)
    assert r.returncode == 2
    assert "is an input folder" in r.stderr
    assert files(folder) == ["a.jpg", "notes.txt", "sub/b.jpg"]


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"),
                    reason="file names ignore case only on Windows and macOS")
def test_output_dir_that_is_an_input_folder_in_another_case(folder):
    r = run("strip", folder, "--output-dir", str(folder).upper())
    assert r.returncode == 2 and "is an input folder" in r.stderr


def test_output_dir_that_is_a_file_is_refused(folder):
    r = run("strip", folder, "--output-dir", folder / "a.jpg")
    assert r.returncode == 2 and "is not a folder" in r.stderr
    r = run("strip", folder / "a.jpg", "--output-dir", folder / "notes.txt")
    assert r.returncode == 2 and "is not a folder" in r.stderr


def test_output_option_is_one_file_only(folder):
    for args in ([folder], [folder / "a.jpg", folder / "sub" / "b.jpg"]):
        r = run("strip", *args, "-o", folder / "x.jpg")
        assert r.returncode == 2
        assert "-o/--output names one output file" in r.stderr and "--output-dir" in r.stderr
    assert files(folder) == ["a.jpg", "notes.txt", "sub/b.jpg"]


def test_output_dir_for_one_file(folder, tmp_path):
    out = tmp_path / "new" / "deeper"
    r = run("strip", folder / "a.jpg", "--output-dir", out)
    assert r.returncode == 0, r.stderr
    assert files(out) == ["a.jpg"]
    assert r.stdout.startswith("metaex strip: ")  # the single-file summary


def test_output_dir_for_one_file_never_writes_over_the_original(folder):
    r = run("strip", folder / "a.jpg", "--output-dir", folder, "--overwrite")
    assert r.returncode == 1
    assert "is the original file" in r.stderr
    assert (folder / "a.jpg").read_bytes() == JPEG


def test_output_on_the_original_is_refused_even_with_overwrite(folder):
    r = run("strip", folder / "a.jpg", "-o", folder / "a.jpg", "--overwrite")
    assert r.returncode == 1 and "is the original file" in r.stderr
    assert (folder / "a.jpg").read_bytes() == JPEG


def test_overwrite_for_one_file(folder):
    (folder / "a_clean.jpg").write_bytes(b"old")
    r = run("strip", folder / "a.jpg")
    assert r.returncode == 1 and "--overwrite" in r.stderr
    assert (folder / "a_clean.jpg").read_bytes() == b"old"
    assert run("strip", folder / "a.jpg", "--overwrite").returncode == 0
    assert verify((folder / "a_clean.jpg").read_bytes()).clean


def test_output_never_lands_on_an_input_file(folder):
    (folder / "a_clean.jpg").write_bytes(JPEG)  # named explicitly: an input
    r = run("strip", folder / "a.jpg", folder / "a_clean.jpg", "--overwrite", "--json")
    assert r.returncode == 1
    data = {os.path.basename(f["path"]): f for f in json.loads(r.stdout)["files"]}
    assert data["a.jpg"]["outcome"] == "refused"
    assert "is an input file of this run" in data["a.jpg"]["reason"]
    assert data["a_clean.jpg"]["outcome"] == "cleaned"
    assert (folder / "a_clean.jpg").read_bytes() == JPEG
    assert verify((folder / "a_clean_clean.jpg").read_bytes()).clean


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"),
                    reason="file names ignore case only on Windows and macOS")
def test_input_files_are_compared_ignoring_case(tmp_path):
    (tmp_path / "A.jpg").write_bytes(JPEG)
    (tmp_path / "a_CLEAN.jpg").write_bytes(JPEG)
    r = run("strip", tmp_path / "A.jpg", tmp_path / "a_CLEAN.jpg", "--overwrite", "--json")
    assert outcomes(r)["A.jpg"] == "refused"
    assert (tmp_path / "a_CLEAN.jpg").read_bytes() == JPEG


def test_two_files_with_the_same_output_path(tmp_path):
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "a.jpg").write_bytes(JPEG)
    out = tmp_path / "out"
    r = run("strip", tmp_path / "one", tmp_path / "two", "--output-dir", out, "--json")
    assert r.returncode == 1
    data = json.loads(r.stdout)["files"]
    assert [f["outcome"] for f in data] == ["cleaned", "refused"]
    assert "already written in this run" in data[1]["reason"]
    assert files(out) == ["a.jpg"]


def test_folders_made_for_a_refused_file_are_removed(tmp_path):
    (tmp_path / "in" / "deep" / "er").mkdir(parents=True)
    (tmp_path / "in" / "deep" / "er" / "broken.jpg").write_bytes(JPEG[:300])
    (tmp_path / "in" / "a.jpg").write_bytes(JPEG)
    out = tmp_path / "out"
    r = run("strip", tmp_path / "in", "--recursive", "--output-dir", out)
    assert r.returncode == 1
    assert files(out) == ["a.jpg"]
    assert sorted(os.listdir(out)) == ["a.jpg"]


def test_in_place_with_yes(folder):
    r = run("strip", folder, "--recursive", "--in-place", "--yes", "--json")
    assert r.returncode == 0
    assert outcomes(r) == {"a.jpg": "cleaned", "notes.txt": "skipped", "b.jpg": "cleaned"}
    assert files(folder) == ["a.jpg", "notes.txt", "sub/b.jpg"]
    assert verify((folder / "a.jpg").read_bytes()).clean
    assert (folder / "notes.txt").read_bytes() == b"plain text\n"


def test_in_place_without_a_terminal_changes_nothing(folder):
    r = run("strip", folder, "--in-place")
    assert r.returncode == 1 and "pass --yes" in r.stderr
    assert (folder / "a.jpg").read_bytes() == JPEG


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("answer, changed", [("yes\n", True), ("no\n", False), ("", False)])
def test_in_place_asks_once_for_the_whole_batch(folder, monkeypatch, capsys, answer, changed):
    monkeypatch.setattr(sys, "stdin", Terminal(answer))
    code = cli.main(["strip", str(folder), "--recursive", "--in-place"])
    err = capsys.readouterr().err
    assert err.count("Type 'yes' to overwrite 3 files: ") == 1
    assert code == 0 if changed else code == 1
    assert ((folder / "a.jpg").read_bytes() != JPEG) is changed
    assert ((folder / "sub" / "b.jpg").read_bytes() != JPEG) is changed
    if not changed:
        assert "not confirmed; nothing changed" in err


def test_rename_per_file(folder):
    r = run("strip", folder, "--recursive", "--rename", "--json")
    assert r.returncode == 0
    names = files(folder)
    assert len(names) == 5
    assert sum(n.startswith("img_") for n in names) == 1
    assert sum(n.startswith("sub/img_") for n in names) == 1


def test_options_for_some_formats_apply_to_those_formats_only(tmp_path):
    (tmp_path / "a.jpg").write_bytes(JPEG)
    (tmp_path / "b.tar").write_bytes(build_tar([tar_file("photo.jpg", JPEG)]))
    r = run("strip", tmp_path, "--deep", "--rebuild-image-data", "--remove-comments",
            "--paranoid", "--fixed-time", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    data = {os.path.basename(f["path"]): f for f in json.loads(r.stdout)["files"]}
    assert data["a.jpg"]["outcome"] == data["b.tar"]["outcome"] == "cleaned"
    assert data["b.tar"]["strip"]["verify"]["verdict"] == "CLEAN"  # --deep cleaned inside
    assert any("--deep cleaned 1 file(s)" in n for n in data["b.tar"]["strip"]["notes"])
    assert os.stat(tmp_path / "a_clean.jpg").st_mtime == 946684800


def test_json_schema(folder):
    r = run("strip", folder, "--json")
    data = json.loads(r.stdout)
    assert data["command"] == "strip" and data["interrupted"] is False
    entry = next(f for f in data["files"] if f["path"].endswith("a.jpg"))
    assert set(entry) == {"path", "outcome", "format", "reason", "output", "strip"}
    assert entry["output"] == str(folder / "a_clean.jpg")
    assert entry["strip"]["verify"]["verdict"] == "CLEAN"
