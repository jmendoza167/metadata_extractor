"""Basic tests for `metaex verify`."""

import json
import subprocess
import sys

from helpers.jpeg import corrupt_scan, kitchen_sink_jpeg, sample_photo
from metaex.formats import jpeg


def run(*args):
    return subprocess.run([sys.executable, "-m", "metaex", "verify", *args],
                          capture_output=True, text=True, timeout=60)


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_clean_file_exits_0(tmp_path):
    cleaned = write(tmp_path, "c.jpg", jpeg.strip(kitchen_sink_jpeg()))
    result = run(str(cleaned))
    assert result.returncode == 0, result.stderr
    assert "Result: CLEAN" in result.stdout
    assert "Kept by design:" in result.stdout
    assert "not compared; pass --original" in result.stdout


def test_leftovers_exit_3(tmp_path):
    dirty = write(tmp_path, "d.jpg", sample_photo())
    result = run(str(dirty))
    assert result.returncode == 3
    assert "Result: NOT CLEAN" in result.stdout
    assert "GPS: Coordinates" in result.stdout


def test_with_original(tmp_path):
    original = kitchen_sink_jpeg()
    o = write(tmp_path, "o.jpg", original)
    c = write(tmp_path, "c.jpg", jpeg.strip(original))
    result = run(str(c), "--original", str(o))
    assert result.returncode == 0
    assert "Image data: matches the original" in result.stdout
    data = json.loads(run(str(c), "--original", str(o), "--json").stdout)
    assert data["clean"] is True
    assert data["image_data_match"] is True
    assert data["image_sha256"] == data["original_image_sha256"]


def test_changed_image_data_is_not_clean(tmp_path):
    original = sample_photo()
    o = write(tmp_path, "o.jpg", original)
    altered = corrupt_scan(jpeg.strip(original))
    c = write(tmp_path, "c.jpg", altered)
    result = run(str(c), "--original", str(o))
    assert result.returncode == 3
    assert "DIFFERS from the original" in result.stdout


def test_errors_exit_1(tmp_path):
    assert run(str(tmp_path / "missing.jpg")).returncode == 1
    paper = write(tmp_path, "a.pdf", b"%PDF-1.7\n")
    result = run(str(paper))
    assert result.returncode == 1
    assert "detected type: PDF. metaex doesn't support this format yet" in result.stderr


def test_json_keys(tmp_path):
    c = write(tmp_path, "c.jpg", jpeg.strip(sample_photo()))
    data = json.loads(run(str(c), "--json").stdout)
    assert set(data) == {"path", "type", "clean", "leftovers", "problems", "kept_by_design",
                         "image_data_match", "image_data_rebuilt", "image_sha256",
                         "original_image_sha256", "notes", "verdict", "unchecked"}
    assert data["image_data_match"] is None and data["verdict"] == "CLEAN"
    assert data["image_data_rebuilt"] is False
