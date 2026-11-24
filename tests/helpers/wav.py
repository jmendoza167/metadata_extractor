"""Build WAV files and their chunks in memory. All metadata is made up
(Jane Doe, jane.doe@example.com, the Eiffel Tower)."""

from __future__ import annotations

PCM, FLOAT, EXTENSIBLE = 0x0001, 0x0003, 0xFFFE
SUBFORMAT_TAIL = bytes.fromhex("000000001000800000aa00389b71")


def chunk(fourcc: str, payload: bytes = b"", pad: bytes = b"\x00") -> bytes:
    """A chunk with its size and, for an odd size, a padding byte."""
    raw = fourcc.encode("latin-1")
    assert len(raw) == 4
    return raw + len(payload).to_bytes(4, "little") + payload + (pad if len(payload) % 2 else b"")


def riff(*chunks: bytes, riff_size: int | None = None) -> bytes:
    body = b"WAVE" + b"".join(chunks)
    size = len(body) if riff_size is None else riff_size
    return b"RIFF" + size.to_bytes(4, "little") + body


def fmt(channels: int = 1, rate: int = 8000, bits: int = 16, tag: int = PCM,
        cb_size: int | None = None, extra: bytes = b"") -> bytes:
    """A fmt chunk: 16 bytes for PCM (unless ``cb_size`` is given), 18 for float."""
    align = channels * ((bits + 7) // 8)
    payload = (tag.to_bytes(2, "little") + channels.to_bytes(2, "little")
               + rate.to_bytes(4, "little") + (rate * align).to_bytes(4, "little")
               + align.to_bytes(2, "little") + bits.to_bytes(2, "little"))
    if cb_size is not None or tag != PCM:
        payload += (cb_size or 0).to_bytes(2, "little") + bytes(cb_size or 0)
    return chunk("fmt ", payload + extra)


def fmt_extensible(channels: int = 2, rate: int = 8000, bits: int = 24, valid: int = 24,
                   mask: int = 3, subformat: int = PCM) -> bytes:
    align = channels * ((bits + 7) // 8)
    payload = (EXTENSIBLE.to_bytes(2, "little") + channels.to_bytes(2, "little")
               + rate.to_bytes(4, "little") + (rate * align).to_bytes(4, "little")
               + align.to_bytes(2, "little") + bits.to_bytes(2, "little")
               + (22).to_bytes(2, "little") + valid.to_bytes(2, "little")
               + mask.to_bytes(4, "little") + subformat.to_bytes(2, "little") + SUBFORMAT_TAIL)
    return chunk("fmt ", payload)


def sine_values(count: int, rate: int = 8000, freq: float = 440, bits: int = 16,
                amplitude: float = 0.3, start: int = 0) -> list[int]:
    """A sine tone computed with integers only (Bhaskara I's approximation,
    within 0.2 % of a sine), so the samples are the same on every machine."""
    peak = int(((1 << (bits - 1)) - 1) * amplitude)
    freq = int(freq)
    out = []
    for n in range(start, start + count):
        q = 2 * ((n * freq) % rate)  # twice the position in the period, of 2 * rate
        a, negative = (q, False) if q < rate else (q - rate, True)
        value = peak * 16 * a * (rate - a) // (5 * rate * rate - 4 * a * (rate - a))
        out.append(-value if negative else value)
    return out


def sine(frames: int = 4000, rate: int = 8000, freq: float = 440, channels: int = 1,
         bits: int = 16, amplitude: float = 0.3) -> bytes:
    """A sine tone as little-endian PCM (8-bit unsigned, 16 or 24-bit
    signed), the same samples in every channel."""
    out = bytearray()
    width = (bits + 7) // 8
    for value in sine_values(frames, rate, freq, bits, amplitude):
        for _ in range(channels):
            if bits == 8:
                out.append(value + 128)
            else:
                out += value.to_bytes(width, "little", signed=True)
    return bytes(out)


def data(samples: bytes) -> bytes:
    return chunk("data", samples)


def info(*pairs: tuple[str, str]) -> bytes:
    """LIST INFO with the given (ID, text) sub-chunks (text ends with 0x00)."""
    return chunk("LIST", b"INFO" + b"".join(chunk(k, v.encode("utf-8") + b"\x00")
                                            for k, v in pairs))


def adtl(labels: list[tuple[int, str]] = (), notes: list[tuple[int, str]] = (),
         texts: list[tuple[int, int, str]] = ()) -> bytes:
    """LIST adtl: labl and note (cue ID, text), ltxt (cue ID, length, text)."""
    subs = [chunk("labl", i.to_bytes(4, "little") + t.encode() + b"\x00") for i, t in labels]
    subs += [chunk("note", i.to_bytes(4, "little") + t.encode() + b"\x00") for i, t in notes]
    subs += [chunk("ltxt", i.to_bytes(4, "little") + n.to_bytes(4, "little") + b"rgn "
                   + bytes(8) + t.encode() + b"\x00") for i, n, t in texts]
    return chunk("LIST", b"adtl" + b"".join(subs))


def _fixed(text: str, size: int) -> bytes:
    raw = text.encode("latin-1")
    assert len(raw) <= size
    return raw + bytes(size - len(raw))


def bext(description: str = "Interview, take 3", originator: str = "ZOOM H5",
         reference: str = "FRZOOM0001234567890", date: str = "2024-05-01",
         time: str = "14:30:00", time_reference: int = 0, version: int = 2,
         umid: bytes = bytes(range(1, 33)), history: str = "A=PCM,F=8000,W=16,M=mono,T=ZOOM H5\r\n"
         ) -> bytes:
    payload = (_fixed(description, 256) + _fixed(originator, 32) + _fixed(reference, 32)
               + _fixed(date, 10) + _fixed(time, 8) + time_reference.to_bytes(8, "little")
               + version.to_bytes(2, "little") + umid + bytes(64 - len(umid))
               + (-2300).to_bytes(2, "little", signed=True) + (500).to_bytes(2, "little")
               + (-100).to_bytes(2, "little", signed=True) + (-1800).to_bytes(2, "little",
                                                                               signed=True)
               + (-2000).to_bytes(2, "little", signed=True) + bytes(180)
               + history.encode("latin-1"))
    return chunk("bext", payload)


IXML = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><BWFXML><IXML_VERSION>2.10</IXML_VERSION>"
        "<PROJECT>Flat hunt</PROJECT><SCENE>Kitchen</SCENE><TAKE>3</TAKE>"
        "<NOTE>Jane Doe talking to the landlord</NOTE>"
        "<LOCATION><LOCATION_NAME>12 Example Street</LOCATION_NAME>"
        "<LOCATION_GPS>48.8584, 2.2945</LOCATION_GPS></LOCATION></BWFXML>")
AXML = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><ebuCoreMain><coreMetadata>"
        "<title>Flat hunt, take 3</title><creator>Jane Doe</creator>"
        "<date>2024-05-01</date></coreMetadata></ebuCoreMain>")


def ixml(text: str = IXML) -> bytes:
    return chunk("iXML", text.encode("utf-8") + b"\x00")


def axml(text: str = AXML) -> bytes:
    return chunk("axml", text.encode("utf-8"))


def pmx(packet: str | bytes) -> bytes:
    return chunk("_PMX", packet.encode("utf-8") if isinstance(packet, str) else packet)


def cue(*points: tuple[int, int]) -> bytes:
    """A cue chunk: (ID, sample offset) per point, in the data chunk."""
    payload = len(points).to_bytes(4, "little")
    for cue_id, sample in points:
        payload += (cue_id.to_bytes(4, "little") + sample.to_bytes(4, "little") + b"data"
                    + bytes(8) + sample.to_bytes(4, "little"))
    return chunk("cue ", payload)


def smpl(*loops: tuple[int, int, int], manufacturer: int = 0x47, product: int = 0x1234,
         sampler_data: bytes = b"", period: int = 125000) -> bytes:
    """A smpl chunk: (ID, start, end) per loop."""
    header = [manufacturer, product, period, 60, 0, 0, 0, len(loops), len(sampler_data)]
    payload = b"".join(v.to_bytes(4, "little") for v in header)
    for loop_id, start, end in loops:
        payload += b"".join(v.to_bytes(4, "little") for v in (loop_id, 0, start, end, 0, 0))
    return chunk("smpl", payload + sampler_data)


def junk(size: int = 28, content: bytes = b"", fourcc: str = "JUNK") -> bytes:
    return chunk(fourcc, content + bytes(size - len(content)))


def wav(*chunks: bytes, samples: bytes | None = None) -> bytes:
    """A mono 16-bit 8 kHz WAV: fmt, the chunks given, data (a 0.5 s tone by default)."""
    return riff(fmt(), *chunks, data(sine() if samples is None else samples))


def kitchen_sink_wav(cover: bytes | None = None) -> bytes:
    """Every kind of WAV metadata metaex reads (a stereo 16-bit tone)."""
    from helpers import id3 as b
    from helpers.jpeg import SAMPLE_XMP, sample_photo

    cover = sample_photo() if cover is None else cover
    tag = b.tag(b.text("TPE1", "Jane Doe", version=3), b.apic(cover, version=3),
                b.popm("jane.doe@example.com", version=3), version=3, padding=16)
    return riff(
        fmt(channels=2), bext(), junk(28, b"hidden in JUNK"),
        info(("INAM", "Flat hunt, take 3"), ("IART", "Jane Doe"), ("ICMT", "Recorded at home"),
             ("ICRD", "2024-05-01"), ("ISFT", "Example Recorder 2.1"), ("IENG", "Jane Doe")),
        ixml(), axml(), pmx(SAMPLE_XMP), chunk("id3 ", tag),
        data(sine(4000, channels=2)),
        cue((1, 0), (2, 2000)), adtl(labels=[(1, "Verse"), (2, "Chorus: Jane's part")],
                                     notes=[(1, "call jane.doe@example.com")],
                                     texts=[(2, 1000, "landlord speaking")]),
        smpl((1, 0, 3999), sampler_data=b"vendor"),
        chunk("PEAK", bytes(24)))


def variants() -> dict[str, bytes]:
    """WAV files with every kind of chunk and format; each one well-formed."""
    from helpers import id3 as b
    from helpers.jpeg import SAMPLE_XMP

    return {
        "pcm16": wav(),
        "pcm8_odd": riff(fmt(bits=8), data(sine(3001, bits=8))),
        "pcm24_stereo": riff(fmt(channels=2, bits=24), data(sine(500, channels=2, bits=24))),
        "pcm_fmt18": riff(fmt(cb_size=0), data(sine(100))),
        "extensible": riff(fmt_extensible(), data(sine(500, channels=2, bits=24))),
        "float": riff(fmt(tag=FLOAT, bits=32), chunk("fact", (500).to_bytes(4, "little")),
                      data(bytes(2000))),
        "info": wav(info(("IART", "Jane Doe"), ("ICMT", "x"))),
        "bext_v0": wav(bext(version=0)),
        "bext_v2": wav(bext()),
        "ixml": wav(ixml()),
        "axml": wav(axml()),
        "pmx": wav(pmx(SAMPLE_XMP)),
        "id3_chunk": wav(chunk("id3 ", b.tag(b.text("TPE1", "Jane Doe")))),
        "id3_upper": wav(chunk("ID3 ", b.tag(b.text("TPE1", "Jane Doe"), version=3))),
        "cue_adtl": wav(cue((1, 0), (2, 100)), adtl(labels=[(1, "a")], notes=[(2, "b")])),
        "smpl": wav(smpl((1, 0, 100))),
        "junk_zero": wav(junk(100), junk(9, fourcc="PAD "), junk(4, fourcc="FLLR")),
        "unknown_chunks": wav(chunk("PEAK", bytes(24)), chunk("zzzz", b"odd")),
        "data_first": riff(fmt(), data(sine(10)), info(("IART", "Jane Doe"))),
        "kitchen_sink": kitchen_sink_wav(),
    }
