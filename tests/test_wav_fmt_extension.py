"""WAV: format-specific bytes in the fmt chunk that a format doesn't define.

Regression test: a PCM fmt chunk whose cbSize announced extension bytes
(PCM has none) carried them into the cleaned copy, which verify called
CLEAN. The fmt chunk is copied byte for byte, so such bytes are now shown,
make verify fail and are refused by strip."""

import pytest

from helpers.wav import FLOAT, chunk, data, fmt, fmt_extensible, riff, sine
from metaex.core.errors import HiddenDataError
from metaex.core.verify import verify
from metaex.formats import wav

SECRET = b"Jane Doe jane.doe@example.com"


def pcm_with_extension(payload: bytes = SECRET) -> bytes:
    plain = fmt()[8:]  # the 16-byte PCM format description
    body = plain + len(payload).to_bytes(2, "little") + payload
    return riff(chunk("fmt ", body), data(sine()))


def test_pcm_extension_is_hidden_data():
    original = pcm_with_extension()
    sections = {s.key for s in wav.parse(original)["sections"]}
    assert "hidden_chunk_data" in sections
    with pytest.raises(HiddenDataError, match="format-specific bytes"):
        wav.strip(original)
    result = verify(original)  # as a cleaned file would be: fails on its own
    assert not result.clean
    assert any(f.label == "Format-specific bytes in the fmt chunk" for f in result.leftovers)
    assert any("format-specific bytes" in p for p in result.problems)


def test_extensible_with_extra_extension_bytes_is_refused():
    good = fmt_extensible()
    body = good[8:]
    grown = body[:16] + (22 + len(SECRET)).to_bytes(2, "little") + body[18:] + SECRET
    with pytest.raises(HiddenDataError):
        wav.strip(riff(chunk("fmt ", grown), data(sine(channels=2, bits=24))))


@pytest.mark.parametrize("fmt_chunk", [fmt(), fmt(cb_size=0), fmt(tag=FLOAT, bits=32),
                                       fmt_extensible()])
def test_standard_fmt_chunks_are_accepted(fmt_chunk):
    bits = 32 if b"\x03\x00" == fmt_chunk[8:10] else 24 if len(fmt_chunk) > 40 else 16
    channels = 2 if len(fmt_chunk) > 40 else 1
    original = riff(fmt_chunk, data(sine(channels=channels, bits=bits)))
    cleaned = wav.strip(original)
    assert verify(cleaned, original).clean
