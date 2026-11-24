"""metaex show and risk on MP3 files, through the command line."""

import json
import subprocess
import sys

import pytest

from helpers import audiotags as at
from helpers import id3 as b
from helpers import mp3
from helpers.jpeg import sample_photo
from metaex.core import filetype as ft
from metaex.formats import mp3 as mp3_format

PHOTO = sample_photo()


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


def kitchen_sink() -> bytes:
    return (b.tag(*b.kitchen_sink_frames(PHOTO), version=3, padding=64,
                  padding_bytes=b"\x00\x00secret")
            + mp3.stream(10) + b"junk!" + mp3.frames(3)
            + b.tag(b.text("TIT2", "Appended"), footer=True)
            + at.ape_kitchen_sink(PHOTO) + at.lyrics3_v2() + at.id3v1())


@pytest.mark.parametrize("data", [mp3.frames(3, layer=1, sample_rate=48000, bitrate=384),
                                  mp3.frames(3, layer=2, bitrate=192),
                                  mp3.frames(3, version=25, sample_rate=8000, bitrate=8)])
def test_every_layer_is_detected(data):
    assert ft.detect(data) == ft.MP3


def test_show_json(tmp_path):
    path = tmp_path / "song.mp3"
    path.write_bytes(kitchen_sink())
    r = metaex("show", "--json", "--structure", path)
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    sections = report["sections"]
    assert sections["file_type"]["fields"]["support"]["value"] == (
        "viewing supported; stripping not implemented yet")
    assert sections["file_type"]["fields"]["embedded_metadata"]["value"] == "examined"
    assert sections["audio"]["fields"]["format"]["value"] == "MPEG-1 Layer III"
    assert sections["encoder_info"]["fields"]["encoder"]["value"] == "LAME3.100"
    assert sections["id3v2"]["title"] == "ID3v2.3 tag 1 (at offset 0)"
    assert sections["id3v2_2"]["title"] == "ID3v2.4 tag 2 (appended at the end)"
    for key in ("ape", "lyrics3", "id3v1", "audio_pictures", "gps", "hidden_tag_data",
                "hidden_audio_data", "structure"):
        assert key in sections, key
    assert any(w.startswith("CRITICAL: 5 bytes of data hidden in the audio stream")
               for w in report["warnings"])


def test_risk(tmp_path):
    path = tmp_path / "song.mp3"
    path.write_bytes(kitchen_sink())
    r = metaex("risk", "--json", path)
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    critical = {(f["section"], f["label"]) for f in result["findings"]
                if f["level"] == "critical"}
    assert ("ID3v2.3 tag 1 (at offset 0)", "Popularimeter (rating by e-mail): "
            "jane.doe@example.com") in critical
    assert any(section.endswith("GPS") for section, _label in critical)
    assert result["can_strip"] is False
    text = metaex("risk", "--no-color", path)
    assert "CRITICAL (" in text.stdout and "metaex can't strip this type of file yet" in text.stdout


def test_a_clean_mp3(tmp_path):
    path = tmp_path / "clean.mp3"
    path.write_bytes(mp3.stream(10))
    result = json.loads(metaex("risk", "--json", path).stdout)
    levels = {f["level"] for f in result["findings"]}
    assert levels == {"low"} and result["summary"]["critical"] == 0


def test_malformed_files_are_shown_with_warnings(tmp_path):
    cases = {
        "truncated.mp3": b.tag(b.text("TPE1", "Jane Doe")) + mp3.frames(5)[:-100],
        "bad_ape.mp3": mp3.frames(5) + at.ape([at.ape_item("Artist", b"x")])[:-20] + b"0" * 20,
    }
    for name, data in cases.items():
        path = tmp_path / name
        path.write_bytes(data)
        r = metaex("show", "--json", path)
        assert r.returncode == 0, r.stderr
        report = json.loads(r.stdout)
        assert report["sections"]["file_type"]["fields"]["embedded_metadata"]["value"] == (
            "partly examined (file is malformed)"), name
    report = json.loads(metaex("show", "--json", tmp_path / "truncated.mp3").stdout)
    assert report["sections"]["id3v2"]["fields"]["TPE1"]["value"] == "Jane Doe"
    assert any("is truncated" in w for w in report["warnings"])


def test_broken_leading_tag(tmp_path):
    path = tmp_path / "broken.mp3"
    path.write_bytes(b.tag(b.text("TPE1", "x"), padding=5000)[:200])
    r = metaex("show", "--json", path)
    report = json.loads(r.stdout)
    assert report["sections"]["file_type"]["fields"]["embedded_metadata"]["value"] == (
        "not examined (file is malformed)")


@pytest.mark.parametrize("data, message", [
    ((mp3.header(bitrate_index=0) + bytes(400)) * 3, "free-format"),
    (b.tag(b.text("TIT2", "x")) + (bytes([0xFF, 0xF1, 0x50, 0x80, 0x02, 0x1F, 0xFC]) + bytes(10))
     * 4, "AAC"),
])
def test_unsupported_variants(tmp_path, data, message):
    path = tmp_path / "odd.mp3"
    path.write_bytes(data)
    report = json.loads(metaex("show", "--json", path).stdout)
    assert report["sections"]["file_type"]["fields"]["embedded_metadata"]["value"] == (
        "not examined (this variant is not supported)")
    assert any(message in w for w in report["warnings"])


def test_strip_is_not_offered_yet(tmp_path):
    path = tmp_path / "song.mp3"
    path.write_bytes(mp3.stream(3))
    r = metaex("strip", path)
    assert r.returncode == 1 and "can't strip MP3 files yet" in r.stderr
    assert not (tmp_path / "song_clean.mp3").exists()


def test_parse_reports_the_structure_in_file_order():
    parsed = mp3_format.parse(kitchen_sink())
    labels = [f.label for f in parsed["structure"].fields]
    assert labels == ["ID3v2 tag", "Audio frames", "Not audio", "Audio frames", "ID3v2 tag",
                      "APE tag", "Lyrics3 tag", "ID3v1 tag"]
    assert parsed["complete"] is True
