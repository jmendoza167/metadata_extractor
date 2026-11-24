"""Build FLAC files in memory: metadata blocks and real, decodable frames.

A small encoder writes each subframe as VERBATIM (the samples as they
are), CONSTANT, FIXED (a fixed predictor of order 0-4) or LPC (given
quantized coefficients), with Rice-coded residuals in one or more
partitions, wasted bits and the stereo decorrelation modes (left/side,
right/side, mid/side). Frames carry correct CRC-8 and CRC-16 values, and
STREAMINFO the MD5 of the samples, so any FLAC decoder plays the files.
The CRCs here are computed bit by bit, independently of metaex's.

All metadata is made up (Jane Doe, jane.doe@example.com, the Eiffel Tower).
"""

from __future__ import annotations

import hashlib
import math

STREAMINFO, PADDING, APPLICATION, SEEKTABLE, VORBIS_COMMENT, CUESHEET, PICTURE = range(7)


def crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def crc16(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


class Bits:
    def __init__(self) -> None:
        self.value = 0
        self.count = 0

    def put(self, value: int, n: int) -> None:
        if n:
            self.value = (self.value << n) | (value & ((1 << n) - 1))
            self.count += n

    def signed(self, value: int, n: int) -> None:
        assert -(1 << (n - 1)) <= value < (1 << (n - 1)), (value, n)
        self.put(value, n)

    def unary(self, zeros: int) -> None:
        self.put(1, zeros + 1)

    def to_bytes(self) -> bytes:
        pad = -self.count % 8
        return (self.value << pad).to_bytes((self.count + pad) // 8, "big")


def coded_number(n: int) -> bytes:
    """FLAC's UTF-8-like coding of a frame or sample number (up to 36 bits)."""
    if n < 0x80:
        return bytes([n])
    for extra in range(1, 7):
        if n < 1 << (6 * extra + 6 - extra):
            lead = (0xFF << (7 - extra)) & 0xFF
            out = [lead | (n >> (6 * extra))]
            out += [0x80 | ((n >> (6 * i)) & 0x3F) for i in reversed(range(extra))]
            return bytes(out)
    raise ValueError(n)


_BLOCK_CODES = {192: 1, 576: 2, 1152: 3, 2304: 4, 4608: 5,
                **{256 << (n - 8): n for n in range(8, 16)}}
_RATE_CODES = {88200: 1, 176400: 2, 192000: 3, 8000: 4, 16000: 5, 22050: 6, 24000: 7,
               32000: 8, 44100: 9, 48000: 10, 96000: 11}
_SIZE_CODES = {8: 1, 12: 2, 16: 4, 20: 5, 24: 6, 32: 7}


def _rice(bits: Bits, residual: list[int], order: int, partition_order: int = 0,
          escape: bool = False) -> None:
    """Residual coding method 0 (4-bit Rice parameters)."""
    bits.put(0, 2)
    bits.put(partition_order, 4)
    parts = 1 << partition_order
    size = (len(residual) + order) >> partition_order
    pos = 0
    for i in range(parts):
        n = size - (order if i == 0 else 0)
        chunk = residual[pos:pos + n]
        pos += n
        folded = [2 * e if e >= 0 else -2 * e - 1 for e in chunk]
        if escape:
            width = max((abs(e) for e in chunk), default=0).bit_length() + 1
            bits.put(15, 4)
            bits.put(width, 5)
            for e in chunk:
                bits.signed(e, width) if width else None
            continue
        mean = sum(folded) / len(folded) if folded else 0
        k = max(0, min(14, int(math.log2(mean)) if mean >= 1 else 0))
        bits.put(k, 4)
        for u in folded:
            bits.unary(u >> k)
            bits.put(u, k)


def _fixed_residual(x: list[int], order: int) -> list[int]:
    coefs = {0: (), 1: (1,), 2: (2, -1), 3: (3, -3, 1), 4: (4, -6, 4, -1)}[order]
    return [x[n] - sum(c * x[n - 1 - j] for j, c in enumerate(coefs))
            for n in range(order, len(x))]


def subframe(x: list[int], sample_bits: int, kind: str = "verbatim", order: int = 2,
             coefs: tuple[int, ...] = (1600, -700), precision: int = 12, shift: int = 10,
             partition_order: int = 0, escape: bool = False, wasted: int = 0) -> Bits:
    bits = Bits()
    if wasted:
        assert all(v % (1 << wasted) == 0 for v in x)
        x = [v >> wasted for v in x]
        sample_bits -= wasted
    type_code = {"constant": 0, "verbatim": 1, "fixed": 8 + order}.get(kind, 31 + len(coefs))
    bits.put(0, 1)
    bits.put(type_code, 6)
    if wasted:
        bits.put(1, 1)
        bits.unary(wasted - 1)
    else:
        bits.put(0, 1)
    if kind == "constant":
        assert len(set(x)) == 1
        bits.signed(x[0], sample_bits)
    elif kind == "verbatim":
        for v in x:
            bits.signed(v, sample_bits)
    elif kind == "fixed":
        for v in x[:order]:
            bits.signed(v, sample_bits)
        _rice(bits, _fixed_residual(x, order), order, partition_order, escape)
    else:  # lpc
        p = len(coefs)
        for v in x[:p]:
            bits.signed(v, sample_bits)
        bits.put(precision - 1, 4)
        bits.signed(shift, 5)
        for c in coefs:
            bits.signed(c, precision)
        residual = [x[n] - (sum(c * x[n - 1 - j] for j, c in enumerate(coefs)) >> shift)
                    for n in range(p, len(x))]
        _rice(bits, residual, p, partition_order, escape)
    return bits


def frame(channels: list[list[int]], *, rate: int = 8000, bits: int = 16, number: int = 0,
          variable: bool = False, assignment: int | None = None, kind: str = "verbatim",
          size_code_from_streaminfo: bool = False, **subframe_options) -> bytes:
    """One frame holding ``channels`` (lists of samples of equal length)."""
    block_size = len(channels[0])
    if assignment is None:
        assignment = len(channels) - 1
    bs_code = _BLOCK_CODES.get(block_size, 6 if block_size <= 256 else 7)
    rate_code = _RATE_CODES.get(rate, 13)
    head = bytearray([0xFF, 0xF8 | variable, bs_code << 4 | rate_code,
                      assignment << 4 | (0 if size_code_from_streaminfo
                                         else _SIZE_CODES[bits]) << 1])
    head += coded_number(number)
    if bs_code == 6:
        head += bytes([block_size - 1])
    elif bs_code == 7:
        head += (block_size - 1).to_bytes(2, "big")
    if rate_code == 13:
        head += rate.to_bytes(2, "big")
    head.append(crc8(bytes(head)))
    if assignment >= 8:
        left, right = channels
        side = [a - b for a, b in zip(left, right)]
        if assignment == 8:
            coded = [(left, bits), (side, bits + 1)]
        elif assignment == 9:
            coded = [(side, bits + 1), (right, bits)]
        else:
            coded = [([(a + b) >> 1 for a, b in zip(left, right)], bits), (side, bits + 1)]
    else:
        coded = [(c, bits) for c in channels]
    body = Bits()
    for samples, width in coded:
        sub = subframe(samples, width, kind, **subframe_options)
        body.put(sub.value, sub.count)
    out = bytes(head) + body.to_bytes()
    return out + crc16(out).to_bytes(2, "big")


def block(block_type: int, payload: bytes, last: bool = False) -> bytes:
    return bytes([block_type | (0x80 if last else 0)]) + len(payload).to_bytes(3, "big") + payload


def streaminfo(*, rate: int = 8000, channels: int = 1, bits: int = 16, total: int = 0,
               min_block: int = 4096, max_block: int = 4096, md5: bytes = bytes(16),
               min_frame: int = 0, max_frame: int = 0) -> bytes:
    v = (rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | total
    return (min_block.to_bytes(2, "big") + max_block.to_bytes(2, "big")
            + min_frame.to_bytes(3, "big") + max_frame.to_bytes(3, "big")
            + v.to_bytes(8, "big") + md5)


def vorbis_comment(vendor: str = "reference libFLAC 1.4.3 20230623",
                   fields: list[tuple[str, str]] = ()) -> bytes:
    def s(text: str) -> bytes:
        raw = text.encode("utf-8")
        return len(raw).to_bytes(4, "little") + raw

    return s(vendor) + len(fields).to_bytes(4, "little") + b"".join(s(f"{k}={v}")
                                                                    for k, v in fields)


def picture(image: bytes, mime: str = "image/jpeg", desc: str = "Cover", kind: int = 3,
            width: int = 48, height: int = 32) -> bytes:
    m, d = mime.encode("latin-1"), desc.encode("utf-8")
    return (kind.to_bytes(4, "big") + len(m).to_bytes(4, "big") + m + len(d).to_bytes(4, "big")
            + d + width.to_bytes(4, "big") + height.to_bytes(4, "big") + (24).to_bytes(4, "big")
            + bytes(4) + len(image).to_bytes(4, "big") + image)


def seektable(*points: tuple[int, int, int], placeholders: int = 0) -> bytes:
    """(sample, byte offset from the first frame, samples in the frame) per point."""
    return (b"".join(s.to_bytes(8, "big") + o.to_bytes(8, "big") + n.to_bytes(2, "big")
                     for s, o, n in points)
            + (b"\xff" * 8 + bytes(10)) * placeholders)


def cuesheet(tracks: list[tuple[int, int, str]] = ((1, 0, "FRXXX2400001"),),
             catalog: str = "1234567890123", lead_out: int = 4000,
             reserved: bytes = b"") -> bytes:
    """A CD cue sheet: (track number, offset, ISRC) per track, plus the
    lead-out track 170. ``reserved`` goes in the header's reserved bytes."""
    out = bytearray(catalog.encode("ascii").ljust(128, b"\x00"))
    out += (88200).to_bytes(8, "big") + b"\x80" + reserved.ljust(258, b"\x00")
    all_tracks = list(tracks) + [(170, lead_out, "")]
    out.append(len(all_tracks))
    for number, offset, isrc in all_tracks:
        out += offset.to_bytes(8, "big") + bytes([number]) + isrc.encode("ascii").ljust(12,
                                                                                   b"\x00")
        out += b"\x00" + bytes(13)
        if number == 170:
            out.append(0)
        else:
            out.append(1)
            out += (0).to_bytes(8, "big") + b"\x01" + bytes(3)
    return bytes(out)


def sine(samples: int = 8000, rate: int = 8000, freq: float = 440.0, bits: int = 16,
         amplitude: float = 0.3, phase: float = 0.0) -> list[int]:
    peak = (1 << (bits - 1)) - 1
    return [round(amplitude * peak * math.sin(2 * math.pi * freq * n / rate + phase))
            for n in range(samples)]


def md5_of(channels: list[list[int]], bits: int = 16) -> bytes:
    """STREAMINFO's MD5: interleaved little-endian signed samples."""
    width = (bits + 7) // 8
    out = bytearray()
    for frame_samples in zip(*channels):
        for v in frame_samples:
            out += v.to_bytes(width, "little", signed=True)
    return hashlib.md5(bytes(out)).digest()


def encode(channels: list[list[int]], *, rate: int = 8000, bits: int = 16,
           block_size: int = 4096, **frame_options) -> tuple[bytes, bytes]:
    """(STREAMINFO payload, frames) for the whole signal, fixed block size."""
    total = len(channels[0])
    frames = []
    for n, start in enumerate(range(0, total, block_size)):
        part = [c[start:start + block_size] for c in channels]
        frames.append(frame(part, rate=rate, bits=bits, number=n, **frame_options))
    sizes = [len(f) for f in frames]
    info = streaminfo(rate=rate, channels=len(channels), bits=bits, total=total,
                      min_block=block_size, max_block=block_size, md5=md5_of(channels, bits),
                      min_frame=min(sizes), max_frame=max(sizes))
    return info, b"".join(frames)


def flac(*blocks: tuple[int, bytes], info: bytes | None = None, frames: bytes | None = None,
         before: bytes = b"", after: bytes = b"") -> bytes:
    """fLaC, STREAMINFO, the other blocks (type, payload), the frames. Without
    ``info`` and ``frames``: one second of a 440 Hz tone at 8 kHz, verbatim."""
    if info is None or frames is None:
        info, frames = encode([sine()])
    all_blocks = [(STREAMINFO, info)] + list(blocks)
    out = b"fLaC" + b"".join(block(t, p, last=(i == len(all_blocks) - 1))
                             for i, (t, p) in enumerate(all_blocks))
    return before + out + frames + after


def kitchen_sink_flac(cover: bytes | None = None) -> bytes:
    """Every kind of FLAC metadata metaex reads: an ID3v2 tag before the
    stream, Vorbis comments, a PICTURE with GPS, an APPLICATION block, a
    seek table, a cue sheet and PADDING with hidden data."""
    from helpers import id3 as b
    from helpers.jpeg import sample_photo

    cover = sample_photo() if cover is None else cover
    tone = sine(8000, phase=0.0)
    info, frames = encode([tone, sine(8000, freq=660.0)], kind="fixed", assignment=10)
    return flac(
        (SEEKTABLE, seektable((0, 0, 4096), placeholders=1)),
        (VORBIS_COMMENT, vorbis_comment(fields=[
            ("TITLE", "Floor plan blues"), ("ARTIST", "Jane Doe"), ("ALBUM", "Demo tapes"),
            ("DATE", "2024-05-01"), ("COMMENT", "Recorded on my phone, first take"),
            ("CONTACT", "jane.doe@example.com"), ("ENCODER", "Example Encoder 1.0"),
            ("LOCATION", "12 Example Street"), ("REPLAYGAIN_TRACK_GAIN", "-6.00 dB")])),
        (PICTURE, picture(cover)),
        (APPLICATION, b"riff" + b"LIST\x10\x00\x00\x00INFOIART\x04\x00\x00\x00Jane"),
        (CUESHEET, cuesheet(lead_out=8000)),
        (PADDING, b"\x00" * 16 + b"hidden in padding" + bytes(15)),
        info=info, frames=frames,
        before=b.tag(b.text("TPE1", "Jane Doe"), b.text("TIT2", "Floor plan blues"),
                     version=3))
