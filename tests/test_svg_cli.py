"""`metaex strip`, `verify` and `risk` on SVG files."""

import json
import os
import subprocess
import sys

import pytest

from helpers.svg import illustrator_svg, inkscape_svg


def run(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *args], capture_output=True,
                          text=True, timeout=60)


@pytest.fixture
def drawing(tmp_path):
    p = tmp_path / "plan.svg"
    p.write_bytes(inkscape_svg())
    return p


def test_strip_svg(drawing):
    original = drawing.read_bytes()
    result = run("strip", str(drawing))
    assert result.returncode == 0, result.stderr
    assert drawing.read_bytes() == original
    out = result.stdout
    for group in ("Metadata", "Comments", "Editor data", "Local file paths", "GPS location",
                  "Embedded images"):
        assert f"  {group} " in out, group
    assert "Warning: kept 1 <title>/<desc> element(s) for accessibility" in out
    assert "Verification: clean" in out and "Drawing unchanged" in out
    assert sorted(os.listdir(drawing.parent)) == ["plan.svg", "plan_clean.svg"]


def test_strip_svg_json_and_verify(drawing):
    data = json.loads(run("strip", str(drawing), "--json").stdout)
    assert data["type"] == "SVG"
    assert data["verify"]["clean"] and data["verify"]["image_data_match"]
    result = run("verify", data["output"], "--original", str(drawing))
    assert result.returncode == 0, result.stdout
    assert "Result: CLEAN" in result.stdout
    assert "Drawing: matches the original" in result.stdout


def test_paranoid_svg(drawing):
    result = run("strip", str(drawing), "--paranoid")
    assert result.returncode == 0, result.stderr
    assert "<title>" not in result.stdout
    assert b"<title" not in drawing.with_name("plan_clean.svg").read_bytes()


def test_linked_local_file_is_refused(tmp_path):
    p = tmp_path / "logo.svg"
    p.write_bytes(illustrator_svg())
    result = run("strip", str(p))
    assert result.returncode == 1
    assert "the drawing uses local files" in result.stderr
    assert "no file written" in result.stderr
    assert os.listdir(tmp_path) == ["logo.svg"]


def test_risk_svg_before_and_after(drawing):
    before = json.loads(run("risk", str(drawing), "--json").stdout)
    assert before["can_strip"] is True and before["summary"]["critical"] >= 3
    assert run("strip", str(drawing)).returncode == 0
    after = json.loads(run("risk", str(drawing.with_name("plan_clean.svg")), "--json").stdout)
    assert after["summary"]["critical"] == 0 and after["summary"]["high"] == 0


def test_doctype_svg_writes_nothing(tmp_path):
    p = tmp_path / "old.svg"
    p.write_bytes(b'<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg"/>')
    result = run("strip", str(p))
    assert result.returncode == 1 and "DOCTYPE" in result.stderr
    assert os.listdir(tmp_path) == ["old.svg"]
