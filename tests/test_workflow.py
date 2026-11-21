"""End-to-end: show -> strip -> verify -> show, plus the strip/verify/risk edge cases."""

import hashlib
import json
import os
import re
import subprocess
import sys

import pytest

from helpers.jpeg import kitchen_sink_jpeg, make_jpeg, sample_photo
from metaex.formats import jpeg

KEPT_SECTIONS = set(jpeg.KEPT_BY_DESIGN)
NOT_METADATA = {"file_type", "file_system", "hashes"}


def metaex(*args, stdin=None):
    return subprocess.run([sys.executable, "-m", "metaex", *args], capture_output=True,
                          text=True, timeout=60, input=stdin)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def listing(directory):
    return sorted(os.listdir(directory))


def sensitive_fields(show_json):
    return [(key, name) for key, section in show_json["sections"].items()
            if key not in NOT_METADATA
            for name, f in section["fields"].items() if f["sensitive"]]


@pytest.mark.parametrize("order", ["<", ">"])
@pytest.mark.parametrize("extra", [[], ["--paranoid"]])
def test_show_strip_verify_show(tmp_path, order, extra):
    original = tmp_path / "photo.jpg"
    original.write_bytes(kitchen_sink_jpeg(order))
    before = sha(original)

    shown = json.loads(metaex("show", str(original), "--json").stdout)
    assert len(sensitive_fields(shown)) > 30

    result = metaex("strip", str(original), *extra)
    assert result.returncode == 0, result.stderr
    cleaned = tmp_path / "photo_clean.jpg"

    verified = metaex("verify", str(cleaned), "--original", str(original), "--json")
    assert verified.returncode == 0
    v = json.loads(verified.stdout)
    assert v["clean"] and v["image_data_match"]

    after = json.loads(metaex("show", str(cleaned), "--json").stdout)
    metadata_sections = set(after["sections"]) - NOT_METADATA
    assert metadata_sections <= KEPT_SECTIONS
    leftover_sensitive = [s for s in sensitive_fields(after) if s[0] not in KEPT_SECTIONS]
    assert leftover_sensitive == []
    assert not [w for w in after["warnings"] if w.startswith("CRITICAL")]

    risk = json.loads(metaex("risk", str(cleaned), "--json").stdout)
    assert risk["summary"]["critical"] == 0 and risk["summary"]["high"] == 0

    assert sha(original) == before  # the original was never touched
    assert listing(tmp_path) == ["photo.jpg", "photo_clean.jpg"]


@pytest.mark.parametrize("order", ["<", ">"])
@pytest.mark.parametrize("extra", [[], ["--paranoid"]])
def test_png_show_strip_verify_show(tmp_path, order, extra):
    from helpers.png import kitchen_sink_png
    from metaex.formats import png

    original = tmp_path / "image.png"
    original.write_bytes(kitchen_sink_png(order))
    before = sha(original)
    shown = json.loads(metaex("show", str(original), "--json").stdout)
    assert len(sensitive_fields(shown)) > 30

    result = metaex("strip", str(original), *extra)
    assert result.returncode == 0, result.stderr
    cleaned = tmp_path / "image_clean.png"
    verified = metaex("verify", str(cleaned), "--original", str(original), "--json")
    assert verified.returncode == 0, verified.stdout
    v = json.loads(verified.stdout)
    assert v["clean"] and v["image_data_match"]

    after = json.loads(metaex("show", str(cleaned), "--json").stdout)
    assert set(after["sections"]) - NOT_METADATA <= set(png.KEPT_BY_DESIGN)
    leftover = [s for s in sensitive_fields(after) if s[0] not in png.KEPT_BY_DESIGN]
    assert leftover == []
    risk = json.loads(metaex("risk", str(cleaned), "--json").stdout)
    assert risk["summary"]["critical"] == 0 and risk["summary"]["high"] == 0
    assert sha(original) == before
    assert listing(tmp_path) == ["image.png", "image_clean.png"]


def test_in_place_with_and_without_yes(tmp_path):
    p = tmp_path / "photo.jpg"
    p.write_bytes(sample_photo())
    before = sha(p)
    refused = metaex("strip", str(p), "--in-place", stdin="yes\n")
    assert refused.returncode == 1
    assert sha(p) == before
    done = metaex("strip", str(p), "--in-place", "--yes")
    assert done.returncode == 0
    assert metaex("verify", str(p)).returncode == 0
    assert listing(tmp_path) == ["photo.jpg"]


def test_existing_output_is_left_alone(tmp_path):
    p = tmp_path / "photo.jpg"
    p.write_bytes(sample_photo())
    existing = tmp_path / "photo_clean.jpg"
    existing.write_bytes(b"someone else's file")
    result = metaex("strip", str(p))
    assert result.returncode == 1
    assert existing.read_bytes() == b"someone else's file"
    assert listing(tmp_path) == ["photo.jpg", "photo_clean.jpg"]


@pytest.mark.parametrize(
    "name,data",
    [
        ("song.flac", b"fLaC" + bytes(40)),
        ("doc.pdf", b"%PDF-1.7\n"),
        ("notes.txt", b"plain text"),
    ],
)
def test_unsupported_type_is_untouched(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    result = metaex("strip", str(p))
    assert result.returncode == 1
    assert "no file written" in result.stderr
    assert p.read_bytes() == data
    assert listing(tmp_path) == [name]


@pytest.mark.parametrize("fraction", [0.05, 0.5, 0.999, None])
def test_malformed_jpeg_writes_nothing(tmp_path, fraction):
    p = tmp_path / "broken.jpg"
    full = sample_photo()
    data = full[: int(len(full) * fraction)] if fraction else make_jpeg(eoi=False)
    assert len(data) < len(full)
    p.write_bytes(data)
    for args in (["strip", str(p)], ["strip", str(p), "--in-place", "--yes"]):
        result = metaex(*args)
        assert result.returncode == 1
        assert "no file written" in result.stderr
    assert p.read_bytes() == data
    assert listing(tmp_path) == ["broken.jpg"]


@pytest.mark.skipif(sys.platform == "win32", reason="directory permissions are POSIX-only")
def test_read_only_destination_folder(tmp_path):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root can write to read-only folders")
    p = tmp_path / "photo.jpg"
    p.write_bytes(sample_photo())
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o555)
    try:
        result = metaex("strip", str(p), "-o", str(locked / "out.jpg"))
        assert result.returncode == 1
        assert "permission denied" in result.stderr
        assert os.listdir(locked) == []
    finally:
        os.chmod(locked, 0o755)


def test_unwritable_destination_is_reported_cleanly(tmp_path, monkeypatch, capsys):
    import tempfile

    from metaex import cli

    p = tmp_path / "photo.jpg"
    p.write_bytes(sample_photo())

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(tempfile, "mkstemp", denied)
    assert cli.main(["strip", str(p)]) == 1
    err = capsys.readouterr().err
    assert "permission denied; nothing written" in err
    assert listing(tmp_path) == ["photo.jpg"]


def test_rename(tmp_path):
    p = tmp_path / "My Trip 2024.jpg"
    p.write_bytes(sample_photo())
    out = json.loads(metaex("strip", str(p), "--rename", "--json").stdout)["output"]
    assert re.fullmatch(r"img_[0-9a-f]{8}\.jpg", os.path.basename(out))
    assert metaex("verify", out, "--original", str(p)).returncode == 0


def test_fixed_time(tmp_path):
    p = tmp_path / "photo.jpg"
    p.write_bytes(sample_photo())
    assert metaex("strip", str(p), "--fixed-time").returncode == 0
    assert os.stat(tmp_path / "photo_clean.jpg").st_mtime == 946684800


def test_verify_exit_codes(tmp_path):
    original = tmp_path / "o.jpg"
    original.write_bytes(sample_photo())
    cleaned = tmp_path / "c.jpg"
    cleaned.write_bytes(jpeg.strip(sample_photo()))
    assert metaex("verify", str(cleaned)).returncode == 0
    assert metaex("verify", str(cleaned), "--original", str(original)).returncode == 0
    assert metaex("verify", str(original)).returncode == 3
    other = tmp_path / "other.jpg"
    other.write_bytes(jpeg.strip(make_jpeg(scan=b"\x01\x02\x03")))
    assert metaex("verify", str(other), "--original", str(original)).returncode == 3
    assert metaex("verify", str(tmp_path / "missing.jpg")).returncode == 1
    assert metaex("verify").returncode == 2
