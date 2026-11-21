"""`metaex strip`, `verify` and `risk` on GIF files."""

import json
import os
import subprocess
import sys

import pytest

from test_gif_strip import metadata_gif


def run(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *args], capture_output=True,
                          text=True, timeout=60)


@pytest.fixture
def image(tmp_path):
    p = tmp_path / "anim.gif"
    p.write_bytes(metadata_gif())
    return p


def test_strip_gif(image):
    original = image.read_bytes()
    result = run("strip", str(image))
    assert result.returncode == 0, result.stderr
    assert image.read_bytes() == original
    out = result.stdout
    for group in ("Comments", "Plain text", "XMP", "Application extensions"):
        assert f"  {group} " in out, group
    trailing = next(line for line in out.splitlines() if "Data after end of image" in line)
    assert trailing.rstrip().endswith("17 B")
    assert "Animation: Frames = 2, Loop count = forever" in out
    assert "Warning: ICC profile names a device (ACME X100)" in out
    assert "Verification: clean" in out and "Image data unchanged" in out
    assert sorted(os.listdir(image.parent)) == ["anim.gif", "anim_clean.gif"]


def test_strip_gif_json_and_verify(image):
    data = json.loads(run("strip", str(image), "--json").stdout)
    assert data["type"] == "GIF"
    assert data["verify"]["clean"] and data["verify"]["image_data_match"]
    assert data["removed_sizes"] == {"Data after end of image": 17}
    result = run("verify", data["output"], "--original", str(image))
    assert result.returncode == 0, result.stdout
    assert "Result: CLEAN" in result.stdout
    assert "Image data: matches the original" in result.stdout


def test_paranoid_gif(image):
    result = run("strip", str(image), "--paranoid")
    assert result.returncode == 0, result.stderr
    assert "ICC profile names a device" not in result.stdout


def test_verify_gif_with_metadata_is_not_clean(image):
    result = run("verify", str(image))
    assert result.returncode == 3
    assert "Result: NOT CLEAN" in result.stdout


def test_risk_gif(image):
    data = json.loads(run("risk", str(image), "--json").stdout)
    assert data["can_strip"] is True
    assert data["summary"]["critical"] >= 2 and data["summary"]["high"] >= 4
    cleaned = image.with_name("anim_clean.gif")
    assert run("strip", str(image)).returncode == 0
    after = json.loads(run("risk", str(cleaned), "--json").stdout)
    assert after["summary"]["critical"] == 0 and after["summary"]["high"] == 0


def test_malformed_gif_writes_nothing(tmp_path):
    p = tmp_path / "broken.gif"
    p.write_bytes(metadata_gif()[:200])
    result = run("strip", str(p))
    assert result.returncode == 1 and "no file written" in result.stderr
    assert os.listdir(tmp_path) == ["broken.gif"]
