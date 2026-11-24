"""WAV: walking RIFF chunks and reading the WAVE metadata in them."""

import json
import subprocess
import sys

import pytest

from helpers import id3 as b
from helpers import wav as w
from helpers.jpeg import SAMPLE_XMP
from metaex.core import filetype as ft
from metaex.core.errors import MalformedFileError
from metaex.core.risk import CRITICAL, assess
from metaex.formats import wav

VARIANTS = w.variants()


def values(data: bytes) -> dict:
    parsed = wav.parse(data)
    return {(s.key, f.key): f.value for s in parsed["sections"] for f in s.fields}


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_every_variant_parses_completely(name):
    parsed = wav.parse(VARIANTS[name])
    assert parsed["complete"], parsed["warnings"]
    s = wav.walk_chunks(VARIANTS[name])
    assert s.error is None and s.end == len(VARIANTS[name])


# -- the audio format ---------------------------------------------------------------------------


def test_pcm_format():
    v = values(w.wav())
    assert v[("wav_format", "format")] == "PCM" and v[("wav_format", "channels")] == 1
    assert v[("wav_format", "sample_rate")] == "8000 Hz" and v[("wav_format", "bits")] == 16
    assert v[("wav_format", "duration")] == "0.500 s"


def test_extensible_format():
    v = values(VARIANTS["extensible"])
    assert v[("wav_format", "format")] == "PCM (extensible)"
    assert v[("wav_format", "channel_mask")] == "0x3"


def test_float_with_fact():
    v = values(VARIANTS["float"])
    assert v[("wav_format", "format")] == "IEEE float"
    assert v[("wav_format", "duration")] == "0.062 s"


def test_bytes_after_the_format_description_are_hidden_data():
    data = w.riff(w.fmt(cb_size=0, extra=b"secret"), w.data(w.sine(10)))
    parsed = wav.parse(data)
    assert any(w_.startswith("CRITICAL: the fmt chunk holds 6 bytes") for w_ in parsed["warnings"])
    assert "hidden_chunk_data" in [s.key for s in parsed["sections"]]


@pytest.mark.parametrize("payload, message", [
    (bytes(10), "fmt chunk is too short"),
    (w.fmt(cb_size=10)[8:-6], "extension bytes follow"),
    ((1).to_bytes(2, "little") + bytes(14), "zero channels"),
])
def test_broken_fmt_chunks(payload, message):
    parsed = wav.parse(w.riff(w.chunk("fmt ", payload), w.data(b"\x00\x00")))
    assert not parsed["complete"]
    assert any(message in x for x in parsed["warnings"])


# -- metadata chunks ---------------------------------------------------------------------------


def test_info():
    v = values(VARIANTS["kitchen_sink"])
    assert v[("wav_info", "IART")] == "Jane Doe" and v[("wav_info", "ISFT")] == (
        "Example Recorder 2.1")
    assert v[("wav_info", "ICRD")] == "2024-05-01"


def test_info_text_in_latin1_and_unknown_ids():
    data = w.wav(w.chunk("LIST", b"INFO" + w.chunk("IART", "Zoë".encode("latin-1") + b"\x00")
                         + w.chunk("IXYZ", b"custom\x00")))
    v = values(data)
    assert v[("wav_info", "IART")] == "Zoë" and v[("wav_info", "IXYZ")] == "custom"


def test_bext():
    v = values(VARIANTS["kitchen_sink"])
    assert v[("wav_bext", "originator")] == "ZOOM H5"
    assert v[("wav_bext", "origination_date")] == "2024-05-01"
    assert v[("wav_bext", "umid")].startswith("010203")
    assert v[("wav_bext", "loudness_value")] == "-23.00"
    assert v[("wav_bext", "coding_history")] == "A=PCM,F=8000,W=16,M=mono,T=ZOOM H5"
    v0 = values(VARIANTS["bext_v0"])
    assert ("wav_bext", "loudness_value") not in v0


def test_short_bext():
    parsed = wav.parse(w.wav(w.chunk("bext", b"too short")))
    assert any("bext chunk at offset" in x for x in parsed["warnings"])


def test_ixml_and_axml():
    v = values(VARIANTS["kitchen_sink"])
    assert v[("wav_ixml", "BWFXML/NOTE")] == "Jane Doe talking to the landlord"
    assert v[("wav_ixml", "BWFXML/LOCATION/LOCATION_GPS")] == "48.8584, 2.2945"
    assert v[("wav_axml", "ebuCoreMain/coreMetadata/creator")] == "Jane Doe"


@pytest.mark.parametrize("text", ["<a><b>unclosed</a>",
                                  '<!DOCTYPE x [<!ENTITY e "Jane">]><x>&e;</x>'])
def test_unreadable_xml_chunks(text):
    parsed = wav.parse(w.wav(w.ixml(text)))
    assert any("iXML chunk could not be read" in x for x in parsed["warnings"])
    v = {(s.key, f.key): f for s in parsed["sections"] for f in s.fields}
    assert "not examined" in v[("wav_ixml", "raw")].note


def test_many_xml_values_are_capped(monkeypatch):
    monkeypatch.setattr(wav, "MAX_XML_FIELDS", 5)
    text = "<x>" + "".join(f"<v{i}>{i}</v{i}>" for i in range(20)) + "</x>"
    parsed = wav.parse(w.wav(w.ixml(text)))
    assert sum(1 for s in parsed["sections"] if s.key == "wav_ixml" for _ in s.fields) == 5


def test_pmx_xmp():
    v = values(w.wav(w.pmx(SAMPLE_XMP)))
    assert v[("xmp", "dc:creator")] == "Jane Doe"


def test_id3_chunks_and_their_pictures():
    parsed = wav.parse(VARIANTS["kitchen_sink"])
    keys = [s.key for s in parsed["sections"]]
    assert "id3v2" in keys and "audio_pictures" in keys and "gps" in keys
    findings = assess(parsed["sections"])
    assert any(f.level == CRITICAL and f.field_key == "POPM:jane.doe@example.com"
               for f in findings)


def test_broken_id3_chunk():
    parsed = wav.parse(w.wav(w.chunk("id3 ", b"not an id3 tag")))
    assert any("is not a valid ID3v2 tag" in x for x in parsed["warnings"])


def test_data_after_the_id3_tag_in_its_chunk():
    parsed = wav.parse(w.wav(w.chunk("id3 ", b.tag(b.text("TPE1", "x")) + b"secret")))
    assert "hidden_chunk_data" in [s.key for s in parsed["sections"]]


def test_cue_points_labels_and_loops():
    v = values(VARIANTS["kitchen_sink"])
    assert v[("wav_cue", "cue_2")] == "sample 2000"
    assert v[("wav_adtl", "labl_2")] == "Chorus: Jane's part"
    assert v[("wav_adtl", "note_1")] == "call jane.doe@example.com"
    assert v[("wav_adtl", "ltxt_2")] == "landlord speaking"
    assert v[("wav_smpl", "loops")] == 1 and v[("wav_smpl", "manufacturer")] == 0x47
    assert v[("wav_smpl", "sampler_data")] == "6 bytes"


@pytest.mark.parametrize("raw, message", [
    (w.chunk("cue ", (5).to_bytes(4, "little") + bytes(24)), "cue chunk says it has 5"),
    (w.chunk("cue ", b"x"), "cue chunk is too short"),
    (w.chunk("smpl", bytes(20)), "smpl chunk is too short"),
    (w.chunk("smpl", bytes(28) + (3).to_bytes(4, "little") + bytes(4)), "smpl chunk says"),
])
def test_broken_cue_and_smpl(raw, message):
    parsed = wav.parse(w.wav(raw))
    assert not parsed["complete"] and any(message in x for x in parsed["warnings"])


# -- padding, unknown chunks, sizes ------------------------------------------------------------


def test_junk_with_hidden_data():
    parsed = wav.parse(VARIANTS["kitchen_sink"])
    assert any(x.startswith("CRITICAL: 14 bytes of data hidden in padding chunks")
               for x in parsed["warnings"])


def test_zero_padding_chunks():
    v = values(VARIANTS["junk_zero"])
    assert [k for (s, k) in v if s == "audio_padding"] == [
        "JUNK_36", "PAD_144", "FLLR_162"]


def test_a_nonzero_padding_byte_after_an_odd_chunk():
    data = w.riff(w.fmt(bits=8), w.chunk("data", b"\x80\x80\x80", pad=b"\x07"))
    parsed = wav.parse(data)
    keys = {(s.key, f.key) for s in parsed["sections"] for f in s.fields}
    assert ("hidden_chunk_data", "pad_36") in keys


def test_odd_chunk_without_padding_at_the_end():
    body = w.fmt(bits=8) + b"data" + (3).to_bytes(4, "little") + b"\x80\x80\x80"
    data = b"RIFF" + (4 + len(body)).to_bytes(4, "little") + b"WAVE" + body
    parsed = wav.parse(data)
    assert any("has no padding byte" in x for x in parsed["warnings"])


def test_unknown_chunks_are_listed():
    v = values(VARIANTS["unknown_chunks"])
    assert v[("unknown", "PEAK@36")] == "24 bytes at offset 36"
    assert ("unknown", "zzzz@68") in v


def test_lists_of_other_types_are_unknown():
    v = values(w.wav(w.chunk("LIST", b"wavl" + w.chunk("data", b"xx"))))
    assert ("unknown", "LIST wavl@36") in v


def test_riff_size_larger_than_the_file():
    data = w.wav()[:-100]
    parsed = wav.parse(data)
    assert not parsed["complete"]
    assert any("the file is truncated" in x for x in parsed["warnings"])


def test_chunk_larger_than_the_riff_data():
    good = w.wav()
    data = good[:40] + (10**6).to_bytes(4, "little") + good[44:]
    parsed = wav.parse(data)
    assert any("'data' chunk at offset 36 is truncated" in x for x in parsed["warnings"])


@pytest.mark.parametrize("trailing, key", [(bytes(10), "audio_padding"),
                                           (b"secret stuff", "audio_trailing")])
def test_data_after_the_riff_end(trailing, key):
    parsed = wav.parse(w.wav() + trailing)
    assert key in [s.key for s in parsed["sections"]]


def test_chunks_appended_outside_the_riff_data_are_named():
    data = w.wav() + w.chunk("id3 ", b.tag(b.text("TPE1", "Jane Doe")))
    v = values(data)
    assert v[("audio_trailing", "content")] == "chunks outside the RIFF data: id3"


def test_many_chunks_are_capped(monkeypatch):
    monkeypatch.setattr(wav, "MAX_CHUNKS", 50)
    parsed = wav.parse(w.wav(*[w.chunk("zzzz")] * 60))
    assert not parsed["complete"] and any("more than 50 chunks" in x for x in parsed["warnings"])


def test_missing_data_chunk():
    parsed = wav.parse(w.riff(w.fmt()))
    assert not parsed["complete"] and "the file has no data chunk" in parsed["warnings"]


@pytest.mark.parametrize("data", [b"RIFF\x04\x00\x00\x00WAVX", b"RIFX", b""])
def test_not_a_wav(data):
    with pytest.raises(MalformedFileError, match="not a WAV file"):
        wav.parse(data)


def test_tiny_riff_size():
    parsed = wav.parse(b"RIFF\x02\x00\x00\x00WAVE")
    assert not parsed["complete"]


# -- refused formats -----------------------------------------------------------------------------


@pytest.mark.parametrize("head, key", [
    (b"RF64\xff\xff\xff\xffWAVEds64", "rf64"), (b"BW64\xff\xff\xff\xffWAVEds64", "rf64"),
    (b"FORM\x00\x00\x00\x10AIFFCOMM", "aiff"), (b"FORM\x00\x00\x00\x10AIFCFVER", "aiff"),
    (b"OggS\x00\x02" + bytes(30), "ogg"),
    (b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00M4A isom" + bytes(8), "m4a"),
])
def test_other_audio_formats_are_refused_clearly(tmp_path, head, key):
    assert ft.detect(head).key == key
    path = tmp_path / "audio.bin"
    path.write_bytes(head + bytes(100))
    r = metaex("strip", path)
    assert r.returncode == 1 and "not supported" in r.stderr and "no file written" in r.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["audio.bin"]
    report = json.loads(metaex("show", "--json", path).stdout)
    assert any("not supported" in x for x in report["warnings"])


def test_show_and_risk_through_the_command_line(tmp_path):
    path = tmp_path / "take3.wav"
    path.write_bytes(VARIANTS["kitchen_sink"])
    report = json.loads(metaex("show", "--json", path).stdout)
    assert report["sections"]["file_type"]["fields"]["embedded_metadata"]["value"] == "examined"
    risk = json.loads(metaex("risk", "--json", path).stdout)
    critical = {f["label"] for f in risk["findings"] if f["level"] == "critical"}
    assert "BWFXML/LOCATION/LOCATION_GPS" in critical and "Note of cue point 1" in critical
