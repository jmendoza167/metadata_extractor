"""verify on many files: pairing with the originals by relative path, files
without a pair, outcomes and exit codes."""

import json
import os
import re
import subprocess
import sys

import pytest

from helpers.jpeg import kitchen_sink_jpeg
from metaex.commands.verify import original_for
from metaex.formats import jpeg

JPEG = kitchen_sink_jpeg()
CLEAN = jpeg.strip(JPEG)


def run(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)], input="",
                          capture_output=True, text=True, encoding="utf-8", timeout=180)


def lines_of(r):
    """stdout lines without the "[i/N] " progress counter."""
    return [re.sub(r"^\[\d+/\d+\] ", "", line) for line in r.stdout.splitlines()]


def results(r):
    return {os.path.basename(f["path"]): f for f in json.loads(r.stdout)["files"]}


@pytest.fixture
def pair(tmp_path):
    """in/ with originals, out/ with cleaned copies (same relative paths)."""
    for rel in ("a.jpg", "sub/b.jpg"):
        (tmp_path / "in" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "out" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "in" / rel).write_bytes(JPEG)
        (tmp_path / "out" / rel).write_bytes(CLEAN)
    return tmp_path / "in", tmp_path / "out"


def test_folders_are_paired_by_relative_path(pair):
    original, cleaned = pair
    r = run("verify", cleaned, "--original", original, "--recursive", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    data = results(r)
    assert data["a.jpg"]["outcome"] == data["b.jpg"]["outcome"] == "clean"
    assert data["b.jpg"]["original"] == str(original / "sub" / "b.jpg")
    assert data["b.jpg"]["verify"]["image_data_match"] is True


def test_changed_image_data_is_not_clean(pair):
    original, cleaned = pair
    other = bytearray(CLEAN)
    other[-10] ^= 0xFF
    (cleaned / "a.jpg").write_bytes(bytes(other))
    r = run("verify", cleaned, "--original", original, "--recursive")
    assert r.returncode == 3
    assert f"not clean: {cleaned / 'a.jpg'}" in lines_of(r)
    assert "  Image data DIFFERS from the original" in lines_of(r)


def test_leftovers_are_listed_and_exit_3(tmp_path):
    (tmp_path / "a.jpg").write_bytes(JPEG)
    (tmp_path / "b.jpg").write_bytes(CLEAN)
    r = run("verify", tmp_path)
    assert r.returncode == 3
    lines = lines_of(r)
    assert f"not clean: {tmp_path / 'a.jpg'}" in lines
    assert f"clean: {tmp_path / 'b.jpg'}" in lines
    assert any(line.startswith("  Leftover: ") for line in lines)
    assert any(line.startswith("  ... and ") for line in lines)


def test_files_without_a_pair_are_reported(pair):
    original, cleaned = pair
    (cleaned / "extra.jpg").write_bytes(CLEAN)
    (original / "only_original.jpg").write_bytes(JPEG)
    r = run("verify", cleaned, "--original", original, "--recursive", "--json")
    assert r.returncode == 0
    data = results(r)
    assert data["extra.jpg"]["outcome"] == "clean"
    assert data["extra.jpg"]["original"] is None
    assert data["extra.jpg"]["reason"].startswith("no original found in ")
    assert data["only_original.jpg"]["outcome"] == "skipped"
    assert data["only_original.jpg"]["reason"] == "original without a cleaned copy (not checked)"
    text = run("verify", cleaned, "--original", original, "--recursive").stdout
    assert f"skipped: {original / 'only_original.jpg'}: original without a cleaned copy" in text
    assert "  Note: no original found in " in text


def test_clean_names_pair_with_their_originals(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(JPEG)
    out = tmp_path / "cleaned"
    out.mkdir()
    (out / "photo_clean.jpg").write_bytes(CLEAN)
    r = run("verify", out, "--original", tmp_path, "--json")
    assert r.returncode == 0
    assert results(r)["photo_clean.jpg"]["original"] == str(tmp_path / "photo.jpg")


@pytest.mark.parametrize("relative, expected", [
    (("a.jpg",), "a.jpg"),
    (("photo_clean.jpg",), "photo.jpg"),
    (("backup_clean.tar.gz",), "backup.tar.gz"),
    (("my_clean.photo_clean.jpg",), "my_clean.photo.jpg"),
    (("missing.jpg",), None),
])
def test_original_for(tmp_path, relative, expected):
    for name in ("a.jpg", "photo.jpg", "backup.tar.gz", "my_clean.photo.jpg"):
        (tmp_path / name).write_bytes(b"x")
    found = original_for(relative, str(tmp_path))
    assert found == (None if expected is None else os.path.join(str(tmp_path), expected))


def test_same_relative_path_wins_over_the_clean_name(tmp_path):
    (tmp_path / "x_clean.jpg").write_bytes(b"1")
    (tmp_path / "x.jpg").write_bytes(b"2")
    assert original_for(("x_clean.jpg",), str(tmp_path)) == str(tmp_path / "x_clean.jpg")


def test_verify_never_skips_clean_outputs(tmp_path):
    (tmp_path / "a_clean.jpg").write_bytes(CLEAN)
    (tmp_path / "b.jpg").write_bytes(CLEAN)
    r = run("verify", tmp_path, "--json")
    assert {k: v["outcome"] for k, v in results(r).items()} == {"a_clean.jpg": "clean",
                                                                "b.jpg": "clean"}


def test_original_must_be_a_folder_for_a_batch(pair):
    original, cleaned = pair
    r = run("verify", cleaned, "--original", original / "a.jpg")
    assert r.returncode == 2
    assert "--original must be the folder of the originals" in r.stderr


def test_unsupported_files(tmp_path):
    (tmp_path / "notes.txt").write_bytes(b"text")
    (tmp_path / "a.jpg").write_bytes(CLEAN)
    r = run("verify", tmp_path, "--json")
    assert r.returncode == 0
    assert results(r)["notes.txt"]["outcome"] == "skipped"
    # named on the command line: refused, as for a single file
    r = run("verify", tmp_path / "notes.txt", tmp_path / "a.jpg", "--json")
    assert r.returncode == 1
    assert results(r)["notes.txt"]["outcome"] == "refused"
    assert "metaex doesn't support this format yet" in results(r)["notes.txt"]["reason"]


def test_malformed_files_are_not_clean_as_for_one_file(tmp_path):
    (tmp_path / "broken.jpg").write_bytes(JPEG[:300])
    r = run("verify", tmp_path)
    assert r.returncode == 3
    assert "  Problem: the file structure is malformed" in r.stdout


def test_errors_win_over_not_clean(tmp_path):
    (tmp_path / "a.jpg").write_bytes(JPEG)
    r = run("verify", tmp_path / "a.jpg", tmp_path / "missing.jpg")
    assert r.returncode == 1
    lines = lines_of(r)
    assert f"not clean: {tmp_path / 'a.jpg'}" in lines
    assert f"error: {tmp_path / 'missing.jpg'}: no such file or folder" in lines


def test_cleaned_folder_inside_the_original_folder(tmp_path):
    (tmp_path / "a.jpg").write_bytes(JPEG)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "a.jpg").write_bytes(CLEAN)
    r = run("verify", tmp_path / "out", "--original", tmp_path, "--recursive", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)["files"]
    assert [(os.path.basename(f["path"]), f["outcome"]) for f in data] == [("a.jpg", "clean")]
