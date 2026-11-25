"""show and risk on many files: one report per file under a header, errors
and refusals per file, never stopping at a bad file."""

import json
import os
import re
import subprocess
import sys

import pytest

from helpers.jpeg import kitchen_sink_jpeg
from metaex.commands import batch
from metaex.core.batch import Collection, Item, Skipped


def run(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "in"
    (root / "sub").mkdir(parents=True)
    (root / "a.jpg").write_bytes(kitchen_sink_jpeg())
    (root / "notes.txt").write_bytes(b"plain text\n")
    (root / "broken.jpg").write_bytes(kitchen_sink_jpeg()[:200])
    (root / "sub" / "b.jpg").write_bytes(kitchen_sink_jpeg())
    return root


def headers(text):
    """The paths in the "==> [i/N] path" headers."""
    return [re.sub(r"^\[\d+/\d+\] ", "", line[4:]) for line in text.splitlines()
            if line.startswith("==> ")]


@pytest.mark.parametrize("command", ["show", "risk"])
def test_each_file_gets_a_header(folder, command):
    r = run(command, folder, "--recursive")
    assert r.returncode == 0, r.stderr
    assert headers(r.stdout) == [str(folder / "a.jpg"), str(folder / "broken.jpg"),
                                 str(folder / "sub" / "b.jpg")]
    title = "metaex report: " if command == "show" else "metaex risk: "
    assert r.stdout.count(title) == 3
    reports = r.stdout.split("\nSummary: ")[0]
    assert "notes.txt" not in reports  # unsupported inside a folder: skipped


@pytest.mark.parametrize("command", ["show", "risk"])
def test_one_level_without_recursive(folder, command):
    r = run(command, folder)
    assert r.returncode == 0
    assert headers(r.stdout) == [str(folder / "a.jpg"), str(folder / "broken.jpg")]


@pytest.mark.parametrize("command", ["show", "risk"])
def test_a_missing_path_is_an_error_and_the_rest_goes_on(folder, command):
    missing = folder / "missing.jpg"
    r = run(command, missing, folder / "a.jpg")
    assert r.returncode == 1
    assert headers(r.stdout) == [str(missing), str(folder / "a.jpg")]
    assert "Error: no such file or folder" in r.stdout


@pytest.mark.parametrize("command", ["show", "risk"])
def test_a_refused_file_does_not_stop_the_batch(folder, command):
    big = folder / "big.jpg"
    big.write_bytes(kitchen_sink_jpeg() + bytes(2 * 1024 * 1024))
    r = run(command, folder, "--max-size", "1")
    assert r.returncode == 1
    assert "Refused: " in r.stdout and "file is too large" in r.stdout
    assert str(folder / "a.jpg") in headers(r.stdout)


@pytest.mark.parametrize("command, key", [("show", "report"), ("risk", "risk")])
def test_json(folder, command, key):
    r = run(command, folder, "--recursive", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["command"] == command and data["interrupted"] is False
    files = {os.path.relpath(f["path"], folder).replace(os.sep, "/"): f for f in data["files"]}
    assert files["a.jpg"]["outcome"] == "examined" and files["a.jpg"]["format"] == "JPEG"
    assert key in files["a.jpg"]
    assert files["notes.txt"]["outcome"] == "skipped"
    assert files["notes.txt"]["reason"] == "unsupported format (detected type: unknown)"
    assert key not in files["notes.txt"]
    assert files["sub/b.jpg"]["outcome"] == "examined"
    if command == "risk":
        assert files["a.jpg"]["risk"]["summary"]["critical"] > 0


def test_unsupported_file_named_in_a_batch_is_skipped_for_show(folder):
    r = run("show", folder / "notes.txt", folder / "a.jpg", "--json")
    assert r.returncode == 0
    outcomes = {os.path.basename(f["path"]): f["outcome"] for f in json.loads(r.stdout)["files"]}
    assert outcomes == {"notes.txt": "skipped", "a.jpg": "examined"}


def test_single_file_output_is_unchanged(folder):
    r = run("show", folder / "a.jpg")
    assert r.returncode == 0
    assert r.stdout.startswith("metaex report: ")
    assert not headers(r.stdout)


def test_extract_thumbnail_needs_one_file(folder):
    r = run("show", folder, "--extract-thumbnail", folder / "thumb.jpg")
    assert r.returncode == 2
    assert "--extract-thumbnail works on one file only" in r.stderr
    assert not (folder / "thumb.jpg").exists()


# -- the runner itself --------------------------------------------------------------------


def items(tmp_path, n):
    out = []
    for i in range(n):
        p = tmp_path / f"{i}.jpg"
        p.write_bytes(kitchen_sink_jpeg())
        out.append(Item(str(p), (p.name,), explicit=False, root=str(tmp_path)))
    return out


def test_an_internal_error_is_reported_for_that_file_only(tmp_path):
    def work(result):
        if result.path.endswith("1.jpg"):
            raise RuntimeError("boom")
        return batch.EXAMINED

    collection = Collection(items=items(tmp_path, 3))
    run_ = batch.run(collection, lambda item: batch.process(
        item, work, max_size=1 << 20, refuse_unsupported=False))
    assert [r.outcome for r in run_.results] == ["examined", "error", "examined"]
    assert run_.results[1].reason == "internal error: RuntimeError: boom"
    assert batch.exit_code(run_) == 1


def test_ctrl_c_stops_with_the_results_so_far(tmp_path):
    seen = []

    def process_item(item):
        if len(seen) == 2:
            raise KeyboardInterrupt
        seen.append(item)
        return batch.FileResult(item.path, batch.EXAMINED)

    collection = Collection(items=items(tmp_path, 5),
                            skipped=[Skipped(str(tmp_path / ".x"), "hidden")])
    run_ = batch.run(collection, process_item)
    assert run_.interrupted and run_.total == 5
    assert [r.outcome for r in run_.results] == ["skipped", "examined", "examined"]
    assert batch.exit_code(run_) == 1


def test_exit_codes():
    def make(*outcomes):
        return batch.BatchRun([batch.FileResult("x", o) for o in outcomes])

    assert batch.exit_code(make("examined", "skipped")) == 0
    assert batch.exit_code(make("cleaned", "refused")) == 1
    assert batch.exit_code(make("clean", "not clean"), not_clean_code=3) == 3
    assert batch.exit_code(make("not clean", "error"), not_clean_code=3) == 1
    assert batch.exit_code(make("not clean")) == 0
