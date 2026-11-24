"""Risk levels of audio metadata (the rules in core/risk.py)."""

import pytest

from helpers import audiotags as at
from helpers import id3 as b
from helpers import mp3
from helpers.jpeg import sample_photo
from metaex.core.risk import AUDIO_SECTIONS, CRITICAL, HIGH, LOW, MEDIUM, assess, classify
from metaex.formats.mp3 import layout, tag_sections


@pytest.mark.parametrize("section, key, value, level", [
    # critical: account and purchase IDs, e-mail addresses
    ("id3v2", "POPM:jane.doe@example.com", "rating 196/255", CRITICAL),
    ("id3v2", "PRIV:WM/UniqueFileIdentifier", "<12 bytes>", CRITICAL),
    ("id3v2", "UFID:http://musicbrainz.org", "0f9b6c4e", CRITICAL),
    ("id3v2", "OWNE", "price paid 'EUR0.99'", CRITICAL),
    ("id3v2", "TXXX:Contact", "jane.doe@example.com", CRITICAL),
    ("id3v2", "COMM:eng:", "mail me: jane.doe@example.com", CRITICAL),
    ("ape", "Comment", "Write to jane.doe@example.com", CRITICAL),
    ("vorbis_comment", "EMAIL", "someone", CRITICAL),
    ("hidden_audio_data", "at_100", "14 bytes", CRITICAL),
    ("hidden_tag_data", "id3v2_0", "6 non-zero bytes", CRITICAL),
    # high: people, dates, comments, lyrics, user URLs, devices, pictures
    ("id3v2", "TPE1", "Jane Doe", HIGH), ("id3v2", "TCOM", "Jane Doe", HIGH),
    ("id3v2", "TENC", "Jane Doe", HIGH), ("id3v2", "TCOP", "2024 Jane Doe", HIGH),
    ("id3v2", "TDRC", "2024-05-01", HIGH), ("id3v2", "TYER", "2024", HIGH),
    ("id3v2", "COMM:eng:", "first take", HIGH), ("id3v2", "USLT:eng:", "la la", HIGH),
    ("id3v2", "SYLT:eng:", "la la", HIGH), ("id3v2", "WXXX:Homepage", "https://x", HIGH),
    ("id3v2", "WOAR", "https://x", HIGH), ("id3v2", "APIC:front cover", "jpeg", HIGH),
    ("id3v2", "GEOB:notes", "26 bytes", HIGH), ("id3v2", "TOFN", "jane take 3.wav", HIGH),
    ("id3v1", "artist", "Jane Doe", HIGH), ("id3v1", "year", "2024", HIGH),
    ("id3v1", "comment", "first take", HIGH),
    ("ape", "Artist", "Jane Doe", HIGH), ("ape", "Record Location", "12 Example St", HIGH),
    ("ape", "Cover Art (Front)", "binary", HIGH), ("ape", "Related", "https://x", HIGH),
    ("lyrics3", "AUT", "Jane Doe", HIGH), ("lyrics3", "LYR", "la la", HIGH),
    ("vorbis_comment", "ARTIST", "Jane Doe", HIGH), ("vorbis_comment", "DATE", "2024", HIGH),
    ("vorbis_comment", "LOCATION", "Home", HIGH),
    ("wav_info", "IART", "Jane Doe", HIGH), ("wav_info", "ICRD", "2024-05-01", HIGH),
    ("wav_info", "ICMT", "first take", HIGH),
    ("wav_bext", "originator", "ZOOM H5", HIGH), ("wav_bext", "origination_date", "2024", HIGH),
    ("wav_bext", "description", "Interview", HIGH),
    ("wav_adtl", "labl_1", "Verse", HIGH), ("audio_pictures", "picture_1", "JPEG", HIGH),
    ("wav_ixml", "BWFXML/NOTE", "x", HIGH), ("wav_ixml", "BWFXML/LOCATION/LOCATION_GPS", "x",
                                             CRITICAL),
    # medium: software, titles, unknown fields, project data
    ("id3v2", "TSSE", "LAME 3.100", MEDIUM), ("id3v2", "TIT2", "Floor plan blues", MEDIUM),
    ("id3v2", "TALB", "Demo tapes", MEDIUM), ("id3v2", "TXXX:Mood board", "blue", MEDIUM),
    ("id3v2", "XYZ1", "8 bytes", MEDIUM), ("id3v2", "PCNT", "7", MEDIUM),
    ("ape", "Tool Name", "Example Tagger", MEDIUM), ("ape", "Some Custom Key", "x", MEDIUM),
    ("vorbis_comment", "ENCODER", "x", MEDIUM), ("wav_info", "ISFT", "Lavf", MEDIUM),
    ("wav_bext", "coding_history", "A=PCM", MEDIUM), ("wav_ixml", "BWFXML/PROJECT", "x", MEDIUM),
    ("wav_axml", "ebuCoreMain/title", "x", MEDIUM), ("flac_application", "riff", "x", MEDIUM),
    ("wav_smpl", "manufacturer", "71", MEDIUM),
    # low: technical
    ("audio", "bitrate", "128 kbit/s", LOW), ("encoder_info", "encoder", "LAME3.100", LOW),
    ("id3v2", "TCON", "Pop", LOW), ("id3v2", "TRCK", "3", LOW), ("id3v2", "TLEN", "1000", LOW),
    ("id3v2", "COMM:eng:iTunNORM", "00000123", LOW),
    ("id3v2", "TXXX:replaygain_track_gain", "-6 dB", LOW),
    ("vorbis_comment", "REPLAYGAIN_TRACK_GAIN", "-6 dB", LOW),
    ("vorbis_comment", "TRACKNUMBER", "3", LOW), ("id3v1", "genre", "13 (Pop)", LOW),
    ("id3v1", "track", "3", LOW), ("wav_cue", "cue_1", "sample 0", LOW),
    ("wav_smpl", "loops", "1", LOW), ("wav_format", "format", "PCM", LOW),
    ("flac_cuesheet", "catalog", "123", LOW), ("wav_bext", "time_reference", "0", LOW),
])
def test_audio_levels(section, key, value, level):
    assert section in AUDIO_SECTIONS
    assert classify(section, key, key, value) == level


def test_a_kitchen_sink_mp3():
    photo = sample_photo()
    data = (b.tag(*b.kitchen_sink_frames(photo), version=3, padding=32,
                  padding_bytes=b"\x00\x00secret")
            + mp3.stream(5) + at.ape_kitchen_sink(photo) + at.lyrics3_v2() + at.id3v1())
    found = layout(data)
    warnings = []
    sections = tag_sections(found.id3v2, warnings, found.end_tags)
    findings = assess(sections)
    critical = {(f.section_key, f.field_key) for f in findings if f.level == CRITICAL}
    assert {("id3v2", "POPM:jane.doe@example.com"), ("id3v2", "PRIV:WM/UniqueFileIdentifier"),
            ("id3v2", "UFID:http://musicbrainz.org"), ("id3v2", "OWNE"),
            ("id3v2", "TXXX:Contact"), ("ape", "Comment"), ("id3v1", "comment"),
            ("hidden_tag_data", "id3v2_0")} <= critical
    assert any(f.section_key == "gps" and f.level == CRITICAL for f in findings)
    levels = {(f.section_key, f.field_key): f.level for f in findings}
    assert levels[("id3v2", "TPE1")] == HIGH and levels[("id3v2", "TSSE")] == MEDIUM
    assert levels[("audio_pictures", "picture_1")] == HIGH
