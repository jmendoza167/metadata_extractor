"""FLAC: decoy frame headers near the end of the audio.

Regression test: to find the last frame, metaex parses frame candidates
from the end of the file, up to 1000 of them, each with up to 65,536
Rice-coded samples per channel, bit by bit. A 37 KB file of decoy headers
(valid CRC-8, a single Rice-coded subframe, a wrong CRC-16) made show take
39 s and strip 158 s. The search now has a budget of parsed samples: such
a file is refused within a second, and real files are not affected."""

from helpers.flac import crc8, flac
from metaex.core.verify import verify
from metaex.formats import flac as flac_format


def decoy(variable: bool, assignment: int) -> bytes:
    """A frame header (65,536 samples, 16-bit, sample rate from STREAMINFO)
    with a valid CRC-8, then a fixed order-0 subframe whose Rice-coded
    residuals (parameter 0) read one bit per sample from the 0xFF bytes after it."""
    header = bytes([0xFF, 0xF9 if variable else 0xF8, 0x70, (assignment << 4) | (4 << 1),
                    0x00, 0xFF, 0xFF])
    return header + bytes([crc8(header)]) + bytes([0x10, 0x00, 0x3F])


def with_decoys(count: int) -> bytes:
    original = flac()
    layout = flac_format.layout(original)
    first = layout.first_frame
    junk = b"".join(decoy(first.variable, first.assignment) for _ in range(count))
    junk += b"\xff" * 10240
    return original[:layout.audio_end] + junk + original[layout.audio_end:]


def test_many_decoys_are_refused_quickly(monkeypatch):
    parsed = []
    real = flac_format._residual

    def counting(bits, block_size, order, budget):
        parsed.append(block_size)
        return real(bits, block_size, order, budget)

    monkeypatch.setattr(flac_format, "_residual", counting)
    layout = flac_format.layout(with_decoys(999))
    assert "too many damaged frames" in (layout.error or "")
    # about MAX_SEARCH_SAMPLES samples, not 999 x 65,536
    assert sum(parsed) <= flac_format.MAX_SEARCH_SAMPLES + 2 * 65536


def test_a_few_decoys_after_the_audio_are_removed():
    data = with_decoys(5)
    cleaned = flac_format.strip(data)
    assert cleaned == flac_format.strip(flac())
    assert verify(cleaned, data).clean
    assert not verify(data).clean  # the decoys are data after the last frame
