"""Cross-check stripped audio files with mutagen, an independent tag reader.

mutagen must find no tags at all in a stripped file, and the same audio
information (length, sample rate, channels, bitrate) as in the original.
mutagen is a test-only dependency (the [dev] extra); if it is missing,
these tests fail instead of being skipped.
"""

from pathlib import Path

import mutagen
import pytest
from mutagen.apev2 import APEv2
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.mp3 import MP3
from mutagen.wave import WAVE

from helpers import audiotags as at
from helpers import flac as f
from helpers import id3 as b
from helpers import mp3 as m
from helpers import wav as w
from metaex.formats import flac, mp3, wav

MP3_FILES = {name: data for name, data in m.variants().items()
             if name not in ("bare", "layer1", "layer2", "mpeg25", "protected")}
MP3_FILES["ape_then_id3v1"] = m.stream(8) + at.ape_kitchen_sink(b"x" * 100) + at.id3v1()
WAV_FILES = w.variants()
FLAC_FILES = f.variants()


def write_pair(tmp_path: Path, name: str, original: bytes, cleaned: bytes) -> tuple[Path, Path]:
    a, c = tmp_path / f"original_{name}", tmp_path / f"stripped_{name}"
    a.write_bytes(original)
    c.write_bytes(cleaned)
    return a, c


def no_ape(path: Path) -> bool:
    try:
        APEv2(path)
    except mutagen.MutagenError:
        return True
    return False


def no_id3(path: Path) -> bool:
    try:
        ID3(path)
    except mutagen.MutagenError:
        return True
    return False


# -- MP3 -------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(MP3_FILES))
@pytest.mark.parametrize("paranoid", [False, True])
def test_mp3(tmp_path, name, paranoid):
    data = MP3_FILES[name]
    original, stripped = write_pair(tmp_path, name + ".mp3", data,
                                    mp3.strip(data, paranoid=paranoid))
    before, after = MP3(original), MP3(stripped)
    assert after.tags is None and no_id3(stripped) and no_ape(stripped)
    assert (after.info.sample_rate, after.info.channels) == (before.info.sample_rate,
                                                             before.info.channels)
    if not paranoid:  # without the Xing frame mutagen estimates the length
        assert (after.info.length, after.info.bitrate) == (before.info.length,
                                                           before.info.bitrate)


def test_mutagen_sees_the_tags_metaex_removes(tmp_path):
    """The checks above are meaningful: mutagen finds these tags in the originals."""
    ks = tmp_path / "kitchen_sink.mp3"
    ks.write_bytes(MP3_FILES["kitchen_sink"])
    tags = MP3(ks).tags
    assert str(tags["TPE1"]) == "Jane Doe" and tags.getall("POPM") and tags.getall("APIC")
    ape = tmp_path / "ape.mp3"
    ape.write_bytes(MP3_FILES["ape_then_id3v1"])
    assert str(APEv2(ape)["Artist"]) == "Jane Doe"
    v1 = tmp_path / "v1.mp3"
    v1.write_bytes(m.stream(4) + at.id3v1())
    assert str(ID3(v1)["TPE1"]) == "Jane Doe"


# -- WAV ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(WAV_FILES))
@pytest.mark.parametrize("paranoid", [False, True])
def test_wav(tmp_path, name, paranoid):
    data = WAV_FILES[name]
    original, stripped = write_pair(tmp_path, name + ".wav", data,
                                    wav.strip(data, paranoid=paranoid))
    before, after = WAVE(original), WAVE(stripped)
    assert after.tags is None
    for attr in ("length", "sample_rate", "channels", "bitrate", "bits_per_sample"):
        assert getattr(after.info, attr) == getattr(before.info, attr), attr


def test_mutagen_sees_the_wav_id3_chunk(tmp_path):
    path = tmp_path / "id3.wav"
    path.write_bytes(w.wav(w.chunk("id3 ", b.tag(b.text("TPE1", "Jane Doe")))))
    assert str(WAVE(path).tags["TPE1"]) == "Jane Doe"


# -- FLAC ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FLAC_FILES))
@pytest.mark.parametrize("paranoid", [False, True])
def test_flac(tmp_path, name, paranoid):
    data = FLAC_FILES[name]
    cleaned = flac.strip(data, paranoid=paranoid)
    if name == "two_id3v2_before":
        # mutagen reads only one ID3v2 tag before "fLaC": compare with the
        # same stream without the tags in front
        data = data[flac.layout(data).start:]
    original, stripped = write_pair(tmp_path, name + ".flac", data, cleaned)
    before, after = FLAC(original), FLAC(stripped)
    assert after.tags is None and after.pictures == [] and no_id3(stripped)
    assert no_ape(stripped)
    kinds = {type(blk).__name__ for blk in after.metadata_blocks}
    assert kinds <= ({"StreamInfo", "SeekTable"} if paranoid
                     else {"StreamInfo", "SeekTable", "CueSheet"})
    for attr in ("length", "sample_rate", "channels", "bits_per_sample", "total_samples",
                 "md5_signature"):
        assert getattr(after.info, attr) == getattr(before.info, attr), attr
    if name not in ("trailing", "tags_after", "id3v1_after", "ape_after"):
        # mutagen counts data after the frames as audio when it computes the bitrate
        assert after.info.bitrate == before.info.bitrate


def test_mutagen_sees_the_flac_metadata(tmp_path):
    path = tmp_path / "kitchen_sink.flac"
    path.write_bytes(FLAC_FILES["kitchen_sink"])
    tags = FLAC(path)
    assert tags["ARTIST"] == ["Jane Doe"] and len(tags.pictures) == 1
