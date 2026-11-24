"""metaex verify on MP3: the same audio frames, byte for byte, and no tags."""

import json
import subprocess
import sys

import pytest

from helpers import audiotags as at
from helpers import id3 as b
from helpers import mp3
from helpers.jpeg import sample_photo
from metaex.core.verify import verify
from metaex.formats.mp3 import strip

PHOTO = sample_photo()
AUDIO = mp3.stream(10)
INFO_LENGTH = mp3.frame_size()


def tagged() -> bytes:
    return (b.tag(*b.kitchen_sink_frames(PHOTO), version=3, padding=64) + AUDIO
            + at.ape_kitchen_sink(PHOTO) + at.id3v1())


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


@pytest.mark.parametrize("paranoid", [False, True])
def test_stripped_files_are_clean_and_match(paranoid):
    original = tagged()
    result = verify(strip(original, paranoid=paranoid), original)
    assert result.clean and result.image_data_match is True, (result.leftovers, result.problems)
    assert result.image_label == "Audio frames"
    assert result.image_sha256 == result.original_image_sha256
    kept = {f.section for f in result.kept_by_design}
    assert kept == ({"Audio"} if paranoid else {"Audio", "Info frame"})


def test_an_unstripped_file_has_leftovers():
    result = verify(tagged())
    assert not result.clean
    sections = {f.section for f in result.leftovers}
    assert {"ID3v2.3 tag (at offset 0)", "MP3 structure"} <= sections
    structure = [f.label for f in result.leftovers if f.section == "MP3 structure"]
    assert structure == ["ID3v2 tag at offset 0", f"APE tag at offset {len(tagged()) - 128 - len(at.ape_kitchen_sink(PHOTO))}",
                         f"ID3v1 tag at offset {len(tagged()) - 128}"]


def test_an_empty_tag_is_a_leftover():
    result = verify(b.tag(padding=10) + AUDIO)
    assert not result.clean
    assert [f.label for f in result.leftovers] == ["ID3v2 tag at offset 0"]


def test_data_between_frames_is_a_leftover():
    result = verify(AUDIO + b"secret")
    assert not result.clean
    assert "Hidden data in the audio stream" in {f.section for f in result.leftovers}
    assert any(f.label == f"data at offset {len(AUDIO)}" for f in result.leftovers)


def test_data_hidden_in_the_information_frame_is_a_leftover():
    result = verify(mp3.xing_frame(3, 0, extra=b"secret") + mp3.frames(2))
    assert any(f.label == "Info frame" and "non-zero bytes" in f.value for f in result.leftovers)


def test_changed_audio_is_detected():
    original = tagged()
    cleaned = bytearray(strip(original))
    cleaned[INFO_LENGTH + 100] ^= 0x01  # one bit of the first audio frame
    result = verify(bytes(cleaned), original)
    assert result.image_data_match is False and not result.clean
    assert "audio frames differ from the original" in result.problems


def test_a_missing_frame_is_detected():
    original = tagged()
    cleaned = strip(original)
    result = verify(cleaned[:INFO_LENGTH] + cleaned[INFO_LENGTH + mp3.frame_size():], original)
    assert result.image_data_match is False


def test_a_changed_information_frame_is_detected():
    original = tagged()
    cleaned = bytearray(strip(original))
    cleaned[40] ^= 0x01  # inside the Xing fields
    result = verify(bytes(cleaned), original)
    assert "the Info frame differs from the original's" in result.problems
    assert result.image_data_match is False


def test_an_information_frame_the_original_did_not_have():
    original = mp3.frames(10)
    cleaned = mp3.xing_frame(11, 0) + original
    result = verify(cleaned, original)
    assert "the Info frame is not in the original" in result.problems


def test_malformed_files():
    result = verify(AUDIO[:-50])
    assert not result.clean
    assert any("not a complete, well-formed MP3" in p for p in result.problems)
    result = verify(strip(tagged()), AUDIO[:-50])
    assert any("original is not a complete, well-formed MP3" in p for p in result.problems)


# -- through the command line -------------------------------------------------------------------


@pytest.mark.parametrize("paranoid", [False, True])
def test_strip_then_verify(tmp_path, paranoid):
    path = tmp_path / "song.mp3"
    path.write_bytes(tagged())
    out = tmp_path / "song_clean.mp3"
    r = metaex("strip", path, *(["--paranoid"] if paranoid else []))
    assert r.returncode == 0, r.stderr
    assert "Audio frames unchanged (SHA-256" in r.stdout
    if paranoid:
        assert "--paranoid removed the Info frame" in r.stdout
    else:
        assert "LAME tag (encoder 'LAME3.100') is kept" in r.stdout
    assert out.read_bytes() == (AUDIO[INFO_LENGTH:] if paranoid else AUDIO)
    v = metaex("verify", out, "--original", path, "--json")
    assert v.returncode == 0, v.stdout
    result = json.loads(v.stdout)
    assert result["clean"] and result["image_data_match"] is True


def test_strip_json_summary(tmp_path):
    path = tmp_path / "song.mp3"
    path.write_bytes(tagged())
    r = metaex("strip", path, "--json")
    assert r.returncode == 0, r.stderr
    summary = json.loads(r.stdout)
    assert {"ID3v2 tags", "APE tag", "ID3v1 tag", "Embedded pictures and objects",
            "GPS location"} <= set(summary["removed"])
    assert summary["verify"]["clean"] is True


def test_tags_followed_by_unknown_data_are_removed_as_hidden_data(tmp_path):
    # the data after the ID3v1 tag isn't a tag, so the tags before it aren't
    # recognized: everything after the last frame is removed as hidden data
    path = tmp_path / "song.mp3"
    path.write_bytes(tagged() + b"trailing")
    r = metaex("strip", path, "--json")
    assert r.returncode == 0, r.stderr
    summary = json.loads(r.stdout)
    assert summary["removed_sizes"]["Hidden data in the audio stream"] > 3000
    assert (tmp_path / "song_clean.mp3").read_bytes() == AUDIO


def test_verify_an_unstripped_file(tmp_path):
    path = tmp_path / "song.mp3"
    path.write_bytes(tagged())
    v = metaex("verify", path)
    assert v.returncode == 3 and "NOT CLEAN" in v.stdout


def test_strip_in_place(tmp_path):
    path = tmp_path / "song.mp3"
    path.write_bytes(tagged())
    r = metaex("strip", path, "--in-place", "--yes")
    assert r.returncode == 0, r.stderr
    assert path.read_bytes() == AUDIO
