"""`metaex strip`, `verify` and `risk` on WebP files."""

import json
import os
import subprocess
import sys

import pytest

from test_webp_strip import metadata_webp


def run(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *args], capture_output=True,
                          text=True, timeout=60)


@pytest.fixture
def image(tmp_path):
    p = tmp_path / "image.webp"
    p.write_bytes(metadata_webp())
    return p


def test_strip_webp(image):
    original = image.read_bytes()
    result = run("strip", str(image))
    assert result.returncode == 0, result.stderr
    assert image.read_bytes() == original
    out = result.stdout
    for group in ("XMP", "GPS location", "Camera and owner", "Unknown chunks"):
        assert f"  {group} " in out, group
    trailing = next(line for line in out.splitlines() if "Data after end of image" in line)
    assert trailing.rstrip().endswith("10 B")
    assert "EXIF: Orientation = Rotated 90" in out
    assert "Animation: Frames = 2, Loop count = forever" in out
    assert "Warning: ICC profile names a device (ACME X100)" in out
    assert "Verification: clean" in out and "Image data unchanged" in out
    assert sorted(os.listdir(image.parent)) == ["image.webp", "image_clean.webp"]


def test_strip_webp_json_and_verify(image):
    data = json.loads(run("strip", str(image), "--json").stdout)
    assert data["type"] == "WebP"
    assert data["verify"]["clean"] and data["verify"]["image_data_match"]
    assert data["removed_sizes"] == {"Data after end of image": 10}
    result = run("verify", data["output"], "--original", str(image))
    assert result.returncode == 0, result.stdout
    assert "Result: CLEAN" in result.stdout
    assert "Image data: matches the original" in result.stdout


def test_verify_webp_with_metadata_is_not_clean(image):
    result = run("verify", str(image))
    assert result.returncode == 3
    assert "Result: NOT CLEAN" in result.stdout


def test_risk_webp_before_and_after(image):
    before = json.loads(run("risk", str(image), "--json").stdout)
    assert before["can_strip"] is True and before["summary"]["critical"] >= 3
    assert run("strip", str(image)).returncode == 0
    after = json.loads(run("risk", str(image.with_name("image_clean.webp")), "--json").stdout)
    assert after["summary"]["critical"] == 0 and after["summary"]["high"] == 0


def test_malformed_webp_writes_nothing(tmp_path):
    p = tmp_path / "broken.webp"
    p.write_bytes(metadata_webp()[:100])
    result = run("strip", str(p))
    assert result.returncode == 1 and "no file written" in result.stderr
    assert os.listdir(tmp_path) == ["broken.webp"]
