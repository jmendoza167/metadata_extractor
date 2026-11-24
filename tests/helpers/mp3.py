"""Build MPEG audio frames and MP3 files in memory.

Frames are silent: the header is followed by all-zero side information and
data, which every decoder plays as silence (Layer III: no Huffman data;
Layers I and II: no bits allocated). An information frame (Xing/Info with a
LAME tag, or VBRI) is written the way LAME and Fraunhofer encoders write it.
"""

from __future__ import annotations

VERSION_BITS = {1: 3, 2: 2, 25: 0}
"""MPEG version (1, 2, or 25 for 2.5) -> header bits."""
LAYER_BITS = {1: 3, 2: 2, 3: 1}
SAMPLE_RATES = {1: (44100, 48000, 32000), 2: (22050, 24000, 16000), 25: (11025, 12000, 8000)}
BITRATES = {
    (1, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (1, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
STEREO, JOINT_STEREO, DUAL_CHANNEL, MONO = 0, 1, 2, 3


def samples_per_frame(version: int = 1, layer: int = 3) -> int:
    if layer == 1:
        return 384
    return 1152 if layer == 2 or version == 1 else 576


def frame_size(*, version: int = 1, layer: int = 3, bitrate: int = 128,
               sample_rate: int = 44100, padding: bool = False) -> int:
    bps = bitrate * 1000
    if layer == 1:
        return (12 * bps // sample_rate + padding) * 4
    return samples_per_frame(version, layer) // 8 * bps // sample_rate + padding


def header(*, version: int = 1, layer: int = 3, bitrate: int = 128, sample_rate: int = 44100,
           padding: bool = False, mode: int = JOINT_STEREO, protected: bool = False,
           private: bool = False, copyright: bool = False, original: bool = True,
           emphasis: int = 0, bitrate_index: int | None = None) -> bytes:
    """A 4-byte frame header (``bitrate_index`` overrides ``bitrate``: 0 for
    free format, 15 for invalid)."""
    table = BITRATES[(1 if version == 1 else 2, layer)]
    index = table.index(bitrate) if bitrate_index is None else bitrate_index
    rate_index = SAMPLE_RATES[version].index(sample_rate)
    b1 = 0xE0 | VERSION_BITS[version] << 3 | LAYER_BITS[layer] << 1 | (0 if protected else 1)
    b2 = index << 4 | rate_index << 2 | padding << 1 | private
    b3 = mode << 6 | copyright << 3 | original << 2 | emphasis
    return bytes([0xFF, b1, b2, b3])


def side_info_size(version: int = 1, layer: int = 3, mode: int = JOINT_STEREO) -> int:
    if layer != 3:
        return 0
    if version == 1:
        return 17 if mode == MONO else 32
    return 9 if mode == MONO else 17


def frame(*, fill: bytes = b"", **kw) -> bytes:
    """One silent frame; ``fill`` replaces the start of the zero bytes after
    the side information (to make a frame that is not silent)."""
    size = frame_size(**{k: kw[k] for k in ("version", "layer", "bitrate", "sample_rate",
                                            "padding") if k in kw})
    head = header(**kw)
    crc = b"\x00\x00" if kw.get("protected") else b""
    side = bytes(side_info_size(kw.get("version", 1), kw.get("layer", 3),
                                kw.get("mode", JOINT_STEREO)))
    body = fill + bytes(size - 4 - len(crc) - len(side) - len(fill))
    assert len(body) >= 0
    return head + crc + side + body


def frames(n: int, **kw) -> bytes:
    """``n`` frames with the padding pattern of a real encoder: a padding
    slot whenever needed to keep the exact bitrate on average."""
    out = []
    version, layer = kw.get("version", 1), kw.get("layer", 3)
    bps, rate = kw.get("bitrate", 128) * 1000, kw.get("sample_rate", 44100)
    slot_bits = 32 if layer == 1 else 8
    per_frame_bits = samples_per_frame(version, layer) * bps  # times 1/rate
    remainder = 0
    for _ in range(n):
        remainder += per_frame_bits % (rate * slot_bits)
        padding = remainder >= rate * slot_bits
        if padding:
            remainder -= rate * slot_bits
        out.append(frame(padding=padding, **kw))
    return b"".join(out)


def crc16_arc(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def lame_tag(*, encoder: bytes = b"LAME3.100", delay: int = 576, padding: int = 1152,
             music_length: int = 0, music_crc: int = 0, lowpass: int = 17000) -> bytes:
    """A LAME tag without its final CRC (34 bytes)."""
    assert len(encoder) == 9
    return (encoder + bytes([0x03, lowpass // 100]) + bytes(8) + b"\x00\x80"
            + (delay << 12 | padding).to_bytes(3, "big") + b"\x00\x00\x00\x00"
            + music_length.to_bytes(4, "big") + music_crc.to_bytes(2, "big"))


def xing_frame(n_frames: int, n_bytes: int, *, tag: bytes = b"Info", lame: bool = True,
               toc: bool = True, quality: int = 0, extra: bytes = b"",
               encoder: bytes = b"LAME3.100", delay: int = 576, padding: int = 1152,
               **kw) -> bytes:
    """A Xing/Info information frame (all four fields), with a LAME tag
    and its CRC; ``extra`` goes after the last field (where zeros belong)."""
    frame_kw = {k: kw[k] for k in ("version", "layer", "bitrate", "sample_rate", "mode")
                if k in kw}
    size = frame_size(**{k: v for k, v in frame_kw.items() if k != "mode"})
    head = header(**frame_kw)
    side = bytes(side_info_size(kw.get("version", 1), 3, kw.get("mode", JOINT_STEREO)))
    toc_bytes = bytes(range(0, 200, 2)) if toc else b""
    flags = 1 | 2 | (4 if toc else 0) | 8
    body = (tag + flags.to_bytes(4, "big") + n_frames.to_bytes(4, "big")
            + n_bytes.to_bytes(4, "big") + toc_bytes + quality.to_bytes(4, "big"))
    out = head + side + body
    if lame:
        out += lame_tag(encoder=encoder, delay=delay, padding=padding,
                        music_length=n_bytes)
        out += crc16_arc(out).to_bytes(2, "big")
    out += extra
    assert len(out) <= size, (len(out), size)
    return out + bytes(size - len(out))


def vbri_frame(n_frames: int, n_bytes: int, entries: int = 4, **kw) -> bytes:
    """A Fraunhofer VBRI information frame (MPEG-1 Layer III)."""
    frame_kw = {k: kw[k] for k in ("version", "bitrate", "sample_rate", "mode") if k in kw}
    size = frame_size(**{k: v for k, v in frame_kw.items() if k != "mode"})
    out = header(**frame_kw) + bytes(32)
    out += (b"VBRI" + (1).to_bytes(2, "big") + (1105).to_bytes(2, "big")
            + (75).to_bytes(2, "big") + n_bytes.to_bytes(4, "big") + n_frames.to_bytes(4, "big")
            + entries.to_bytes(2, "big") + (1).to_bytes(2, "big") + (2).to_bytes(2, "big")
            + (1).to_bytes(2, "big") + b"".join((n_bytes // entries).to_bytes(2, "big")
                                                 for _ in range(entries)))
    return out + bytes(size - len(out))


def stream(n: int = 20, *, info: bool = True, **kw) -> bytes:
    """``n`` silent audio frames, after an Info frame with a LAME tag by default."""
    audio = frames(n, **kw)
    if not info:
        return audio
    first = xing_frame(n + 1, 0, **kw)
    return xing_frame(n + 1, len(first) + len(audio), **kw) + audio


# -- whole files -------------------------------------------------------------------------------


def kitchen_sink_mp3(cover: bytes | None = None) -> bytes:
    """Every kind of MP3 metadata metaex reads: an ID3v2.3 tag with every
    kind of frame (a cover photo with GPS, an e-mail address in POPM, PRIV,
    UFID, OWNE) and data hidden in its padding, an Info frame with a LAME
    tag, junk between frames, an appended ID3v2.4 tag, an APEv2 tag (with
    the cover again), Lyrics3 v2, TAG+ and ID3v1.1."""
    from helpers import audiotags as at
    from helpers import id3 as b
    from helpers.jpeg import sample_photo

    cover = sample_photo() if cover is None else cover
    return (b.tag(*b.kitchen_sink_frames(cover), version=3, padding=64,
                  padding_bytes=b"\x00\x00hidden note")
            + stream(20) + b"hidden between frames" + frames(5)
            + b.tag(b.text("TIT2", "Floor plan blues (appended)"),
                    b.text("TPE1", "Jane Doe"), footer=True)
            + at.ape_kitchen_sink(cover) + at.lyrics3_v2() + at.tag_plus() + at.id3v1())


def variants() -> dict[str, bytes]:
    """MP3 files with every kind of tag, in every position, and every kind of
    stream; each one well-formed."""
    from helpers import audiotags as at
    from helpers import id3 as b
    from helpers.jpeg import sample_photo

    photo = sample_photo()
    audio = stream(8)
    text = b.text("TPE1", "Jane Doe")
    return {
        "bare": frames(8),
        "info_frame": audio,
        "xing_vbr": xing_frame(9, 0, tag=b"Xing") + frames(4, bitrate=128)
        + frames(4, bitrate=192),
        "vbri": vbri_frame(9, 3000) + frames(8),
        "layer1": frames(8, layer=1, sample_rate=48000, bitrate=384),
        "layer2": frames(8, layer=2, bitrate=192, mode=MONO),
        "mpeg2": stream(8, version=2, sample_rate=24000, bitrate=64),
        "mpeg25": frames(8, version=25, sample_rate=8000, bitrate=8, mode=MONO),
        "protected": frames(8, protected=True),
        "id3v22": b.tag(b.frame("TP1", b"\x00Jane Doe", version=2),
                        b.apic(photo, version=2), version=2) + audio,
        "id3v23": b.tag(*b.kitchen_sink_frames(photo), version=3) + audio,
        "id3v24": b.tag(*b.kitchen_sink_frames(photo, version=4), version=4) + audio,
        "id3v23_unsync": b.tag(b.apic(photo, version=3), text, version=3,
                               unsynchronise=True) + audio,
        "id3v24_unsync_frames": b.tag(b.unsync_frame_v24("APIC", b"\x00image/jpeg\x00\x03\x00"
                                                         + photo)) + audio,
        "id3v23_extended": b.tag(b.text("TIT2", "x", version=3), version=3,
                                 extended=b.extended_v3(32), padding=32) + audio,
        "id3v24_extended_footer": b.tag(text, extended=b.extended_v4(), footer=True) + audio,
        "compressed_encrypted": b.tag(b.compressed_frame("TPE1", b"\x03Jane Doe" * 10),
                                      b.encrypted_frame("TCOM", b"\x01\x02secret")) + audio,
        "itunes_sizes": b.tag(b.frame("TIT2", b"\x03" + b"x" * 200, plain_size=True),
                              text) + audio,
        "chapters": b.tag(b.ctoc("toc", ["c1"]), b.chap("c1", 0, 500, b.text("TIT2", "One")))
        + audio,
        "two_leading_tags": b.tag(text, version=3) + b.tag(text) + audio,
        "appended_v24": audio + b.tag(text, footer=True),
        "id3v1": audio + at.id3v1(),
        "id3v1_0": audio + at.id3v1(track=None),
        "tag_plus": audio + at.tag_plus(title=" more") + at.id3v1(title="T" * 30),
        "apev2": audio + at.ape_kitchen_sink(photo),
        "apev2_no_header": audio + at.ape([at.ape_item("Artist", b"Jane Doe")], header=False),
        "apev1": audio + at.ape([at.ape_item("Artist", b"Jane Doe")], version=1000),
        "lyrics3_v1": audio + at.lyrics3_v1() + at.id3v1(),
        "lyrics3_v2": audio + at.lyrics3_v2() + at.id3v1(),
        "end_order_1": audio + at.ape_kitchen_sink(photo) + at.lyrics3_v2() + at.id3v1(),
        "end_order_2": audio + at.lyrics3_v2() + at.ape([at.ape_item("Artist", b"x")])
        + b.tag(text, footer=True) + at.id3v1(),
        "zero_padding_before_audio": b.tag(text) + bytes(300) + audio,
        "junk": audio + b"hidden" + frames(3) + b"trailing",
        "kitchen_sink": kitchen_sink_mp3(photo),
    }
