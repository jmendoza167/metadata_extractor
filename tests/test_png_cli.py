"""`metaex strip`, `verify` and `risk` on PNG files."""

import json
import os
import subprocess
import sys

import pytest

from helpers.png import chunk, make_png, text
from metaex.formats import png
from test_png_strip import metadata_png


def run(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *args], capture_output=True,
                          text=True, timeout=60)


@pytest.fixture
def image(tmp_path):
    p = tmp_path / "image.png"
    p.write_bytes(metadata_png())
    return p


def test_strip_png(image):
    original = image.read_bytes()
    result = run("strip", str(image))
    assert result.returncode == 0, result.stderr
    out = image.with_name("image_clean.png")
    assert image.read_bytes() == original
    assert png.image_data(out.read_bytes()) == png.image_data(original)
    text_out = result.stdout
    for group in ("Text", "AI generation data", "XMP", "GPS location", "Camera and owner",
                  "Modification time", "Content Credentials (C2PA)", "Unknown chunks",
                  "Color and display"):
        assert f"  {group} " in text_out, group
    trailing = next(line for line in text_out.splitlines() if "Data after end of image" in line)
    assert trailing.rstrip().endswith("10 B")
    assert "Kept by design:" in text_out and "ICC profile" in text_out
    assert "Warning: ICC profile names a device (ACME X100)" in text_out
    assert "Verification: clean" in text_out and "Image data unchanged" in text_out
    assert sorted(os.listdir(image.parent)) == ["image.png", "image_clean.png"]


def test_strip_png_json_and_verify(image):
    data = json.loads(run("strip", str(image), "--json").stdout)
    assert data["type"] == "PNG"
    assert data["verify"]["clean"] and data["verify"]["image_data_match"]
    assert data["removed_sizes"] == {"Data after end of image": 10}
    out = data["output"]
    result = run("verify", out, "--original", str(image))
    assert result.returncode == 0, result.stdout
    assert "Result: CLEAN" in result.stdout
    assert "Image data: matches the original" in result.stdout


def test_verify_png_with_metadata_is_not_clean(image):
    result = run("verify", str(image))
    assert result.returncode == 3
    assert "Result: NOT CLEAN" in result.stdout
    assert "tEXt chunk is not allowed" in result.stdout


def test_risk_png(image):
    result = run("risk", str(image), "--json")
    data = json.loads(result.stdout)
    assert data["can_strip"] is True
    levels = {(f["section"], f["label"]): f["level"] for f in data["findings"]}
    assert levels[("Text", "Author")] == "high"
    assert levels[("AI generation data", "parameters")] == "high"
    assert levels[("Content Credentials", "C2PA manifest")] == "high"
    assert levels[("Trailing data", "Size")] == "critical"
    text_result = run("risk", str(image))
    assert text_result.stdout.splitlines()[-1] == f'Run "metaex strip {image}" to remove these.'


def test_paranoid_and_no_orientation(image):
    result = run("strip", str(image), "--paranoid", "--no-orientation", "--json")
    data = json.loads(result.stdout)
    kept = {(k["section"], k["label"]) for k in data["kept_by_design"]}
    assert kept == {("Color and display", "sRGB")}
    assert data["warnings"] == []


@pytest.mark.parametrize(
    "data,message",
    [
        (make_png(chunk("tEXt", b"a\x00b", crc=5)), "bad CRC"),
        (make_png(iend=False), "missing IEND"),
    ],
)
def test_damaged_png_is_refused(tmp_path, data, message):
    p = tmp_path / "bad.png"
    p.write_bytes(data)
    result = run("strip", str(p))
    assert result.returncode == 1
    assert message in result.stderr and "no file written" in result.stderr
    assert os.listdir(tmp_path) == ["bad.png"]


def test_show_says_supported(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(make_png(text("Author", "x")))
    data = json.loads(run("show", str(p), "--json").stdout)
    assert data["sections"]["file_type"]["fields"]["support"]["value"] == "supported"


def test_kept_color_chunks_are_summarized_by_name(image):
    result = run("strip", str(image))
    line = next(x for x in result.stdout.splitlines() if x.startswith("  Color and display:"))
    assert line == ("  Color and display: kept so the image displays correctly: sRGB, Gamma, "
                    "Chromaticities, Color coding (cICP), Mastering display, "
                    "Content light level, Pixel density")


def test_renamed_icc_profile_name_is_listed_as_removed(image):
    result = run("strip", str(image))
    assert result.returncode == 0, result.stderr
    line = next(l for l in result.stdout.splitlines() if l.startswith("  ICC profile "))
    assert "Profile name" in line and "Jane's display" in line
    assert '(replaced with "ICC profile")' in line
    data = json.loads(run("strip", str(image), "-o", str(image.with_name("b.png")),
                          "--json").stdout)
    assert data["removed"]["ICC profile"] == [
        'Profile name "Jane\'s display" (replaced with "ICC profile")']


def test_generic_icc_profile_name_is_not_listed_as_removed(tmp_path):
    from helpers.jpeg import icc_profile
    from helpers.png import iccp

    p = tmp_path / "g.png"
    p.write_bytes(make_png(text("Author", "x"), before_idat=(iccp(icc_profile()),)))
    data = json.loads(run("strip", str(p), "--json").stdout)
    assert "ICC profile" not in data["removed"]
    assert data["removed"]["Text"] == ["Author"]


def hidden_png(tmp_path):
    import zlib

    from helpers.png import SIGNATURE, ihdr, raw_image

    p = tmp_path / "hidden.png"
    raw = raw_image(16, 16)
    p.write_bytes(SIGNATURE + ihdr(16, 16) + text("Author", "Jane")
                  + chunk("IDAT", zlib.compress(raw + b"secret message here")) + chunk("IEND"))
    return p


def test_hidden_image_data_is_refused_with_an_explanation(tmp_path):
    p = hidden_png(tmp_path)
    result = run("strip", str(p))
    assert result.returncode == 1
    assert "hides 19 extra bytes that viewers don't show" in result.stderr
    assert "--rebuild-image-data" in result.stderr and "no file written" in result.stderr
    assert os.listdir(tmp_path) == ["hidden.png"]


def test_rebuild_image_data_cli(tmp_path):
    p = hidden_png(tmp_path)
    result = run("strip", str(p), "--rebuild-image-data")
    assert result.returncode == 0, result.stderr
    assert "Hidden data in image data  19 B" in result.stdout
    assert "Image data rebuilt, pixels identical" in result.stdout
    cleaned = tmp_path / "hidden_clean.png"
    verified = run("verify", str(cleaned), "--original", str(p))
    assert verified.returncode == 0, verified.stdout
    assert "Image data: rebuilt, pixels identical" in verified.stdout
    as_json = json.loads(run("verify", str(cleaned), "--original", str(p), "--json").stdout)
    assert as_json["image_data_rebuilt"] is True and as_json["image_data_match"] is True
    risk = json.loads(run("risk", str(p), "--json").stdout)
    assert any(f["section"] == "Hidden data in image data" and f["level"] == "critical"
               for f in risk["findings"])
    risk_text = run("risk", str(p)).stdout
    assert "--rebuild-image-data\" to remove these" in risk_text


def test_rebuild_image_data_is_png_only(tmp_path):
    from helpers.jpeg import sample_photo

    p = tmp_path / "photo.jpg"
    p.write_bytes(sample_photo())
    result = run("strip", str(p), "--rebuild-image-data")
    assert result.returncode == 1
    assert "--rebuild-image-data is not available for JPEG files" in result.stderr
    assert os.listdir(tmp_path) == ["photo.jpg"]
