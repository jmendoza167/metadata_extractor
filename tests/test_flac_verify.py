"""metaex verify on FLAC: STREAMINFO and every byte of the frames unchanged."""

import json
import subprocess
import sys

import pytest

from helpers import audiotags as at
from helpers import flac as f
from helpers import id3 as b
from helpers.jpeg import sample_photo
from metaex.core.verify import verify
from metaex.formats import flac
from metaex.formats.flac import strip

KITCHEN = f.kitchen_sink_flac(sample_photo())


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


@pytest.mark.parametrize("paranoid", [False, True])
def test_stripped_files_are_clean_and_match(paranoid):
    result = verify(strip(KITCHEN, paranoid=paranoid), KITCHEN)
    assert result.clean and result.image_data_match is True, (result.leftovers,
                                                              result.problems)
    assert result.image_label == "Audio (STREAMINFO and frames)"
    kept = {fl.section for fl in result.kept_by_design}
    assert kept == ({"Audio", "Seek table"} if paranoid else {"Audio", "Seek table",
                                                               "Cue sheet"})


def test_an_unstripped_file_has_leftovers():
    result = verify(KITCHEN)
    assert not result.clean
    structure = [fl.label for fl in result.leftovers if fl.section == "FLAC structure"]
    assert structure[0] == "ID3v2 tag at offset 0"
    assert any(label.startswith("VORBIS_COMMENT block") for label in structure)
    assert any(label.startswith("PADDING block") for label in structure)
    assert "Vorbis comments" in {fl.section for fl in result.leftovers}


@pytest.mark.parametrize("data, label", [
    (f.flac((f.PADDING, bytes(10))), "PADDING block"),
    (f.flac((f.APPLICATION, b"test")), "APPLICATION block"),
    (f.flac((9, b"x")), "Block of reserved type 9"),
    (f.flac((f.CUESHEET, f.cuesheet(reserved=b"\x01"))), "CUESHEET block"),
    (f.flac(after=at.id3v1()), "ID3v1 tag"),
    (f.flac(after=bytes(4)), "data at offset"),
    (f.flac(before=b.tag(padding=10)), "ID3v2 tag at offset 0"),
])
def test_leftovers(data, label):
    result = verify(data)
    assert not result.clean
    assert any(fl.label.startswith(label) for fl in result.leftovers), result.leftovers


def test_changed_streaminfo_md5_is_detected():
    cleaned = bytearray(strip(KITCHEN))
    cleaned[8 + 20] ^= 1  # inside STREAMINFO's MD5
    result = verify(bytes(cleaned), KITCHEN)
    assert result.image_data_match is False
    assert "STREAMINFO differs from the original" in result.problems


def test_changed_frames_are_detected():
    original = f.flac()
    info, frames = f.encode([f.sine(phase=0.5)])
    other = f.flac(info=flac.layout(original).blocks[0].data, frames=frames)
    result = verify(other, original)
    assert result.image_data_match is False
    assert "the audio frames differ from the original" in result.problems


def test_malformed_files():
    result = verify(f.flac()[:-10])
    assert any("not a complete, well-formed FLAC" in p for p in result.problems)
    result = verify(strip(KITCHEN), KITCHEN[:-10])
    assert any("original is not a complete, well-formed FLAC" in p for p in result.problems)


# -- through the command line -------------------------------------------------------------------


@pytest.mark.parametrize("paranoid", [False, True])
def test_strip_then_verify(tmp_path, paranoid):
    path = tmp_path / "song.flac"
    path.write_bytes(KITCHEN + b"trailing")
    r = metaex("strip", path, *(["--paranoid"] if paranoid else []))
    assert r.returncode == 0, r.stderr
    assert "Audio (STREAMINFO and frames) unchanged (SHA-256" in r.stdout
    if paranoid:
        assert "--paranoid removed the cue sheet" in r.stdout
    else:
        assert "the cue sheet is kept" in r.stdout
    v = metaex("verify", tmp_path / "song_clean.flac", "--original", path, "--json")
    assert v.returncode == 0, v.stdout
    assert json.loads(v.stdout)["image_data_match"] is True


def test_strip_summary_groups(tmp_path):
    path = tmp_path / "song.flac"
    path.write_bytes(KITCHEN + b"trailing")
    summary = json.loads(metaex("strip", path, "--json").stdout)
    assert {"Vorbis comments", "Application blocks", "Embedded pictures and objects",
            "GPS location", "ID3v2 tags", "Hidden data in metadata blocks",
            "Data after the end of the audio"} <= set(summary["removed"])
    assert summary["removed_sizes"]["Data after the end of the audio"] == len(b"trailing")
