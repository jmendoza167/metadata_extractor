"""Stored deflate blocks (helpers.deflate), which make the PNG samples
byte-identical on every machine: metaex's own inflate code must read them
in every place the samples use them (IDAT, APNG fdAT, zTXt, iTXt, iCCP),
and Pillow must decode them to the expected pixels."""

import struct
import zlib

import pytest

from helpers import png
from helpers.deflate import MAX_STORED_BLOCK, ZLIB_HEADER, stored_zlib
from helpers.jpeg import icc_profile
from helpers.pixels import decode_frames
from metaex.core.inflate import Budget, inflate, inflate_stream
from metaex.core.verify import verify
from metaex.formats import png as png_format

SIZES = [0, 1, 100, MAX_STORED_BLOCK - 1, MAX_STORED_BLOCK, MAX_STORED_BLOCK + 1, 200_000]


def payload(size: int) -> bytes:
    return bytes((x * x + 3 * x) % 251 for x in range(size))


def stored_blocks(stream: bytes) -> list[tuple[bool, int]]:
    """(final, length) of every block, read by hand."""
    blocks, pos = [], len(ZLIB_HEADER)
    while True:
        header = stream[pos]
        assert header in (0, 1), "not a stored block on a byte boundary"
        length, nlength = struct.unpack("<HH", stream[pos + 1 : pos + 5])
        assert nlength == length ^ 0xFFFF
        blocks.append((header == 1, length))
        pos += 5 + length
        if header == 1:
            break
    assert len(stream) == pos + 4  # just the Adler-32 checksum left
    return blocks


# -- the zlib streams ---------------------------------------------------------------------


@pytest.mark.parametrize("size", SIZES)
def test_stored_zlib_is_valid_zlib_data(size):
    data = payload(size)
    stream = stored_zlib(data)
    assert int.from_bytes(stream[:2], "big") % 31 == 0  # zlib header check bits
    assert zlib.decompress(stream) == data
    assert stream[-4:] == struct.pack(">I", zlib.adler32(data))
    blocks = stored_blocks(stream)
    assert sum(n for _, n in blocks) == size
    assert all(n <= MAX_STORED_BLOCK for _, n in blocks)
    assert [final for final, _ in blocks] == [False] * (len(blocks) - 1) + [True]
    assert len(blocks) == max(1, -(-size // MAX_STORED_BLOCK))


def test_stored_zlib_never_depends_on_the_zlib_build():
    """The bytes are fixed by the format alone: header, block headers, the
    data and its checksum."""
    data = b"hello"
    assert stored_zlib(data) == (b"\x78\x01" + b"\x01\x05\x00\xfa\xff" + data
                                 + struct.pack(">I", zlib.adler32(data)))
    assert stored_zlib(b"") == b"\x78\x01\x01\x00\x00\xff\xff\x00\x00\x00\x01"


def test_stored_zlib_block_size():
    stream = stored_zlib(payload(20), block_size=7)
    assert stored_blocks(stream) == [(False, 7), (False, 7), (True, 6)]
    for bad in (0, MAX_STORED_BLOCK + 1):
        with pytest.raises(ValueError):
            stored_zlib(b"x", block_size=bad)


@pytest.mark.parametrize("size", SIZES)
def test_metaex_inflate_reads_stored_blocks(size):
    data = payload(size)
    result = inflate(stored_zlib(data), Budget())
    assert result.data == data and result.extra == 0


@pytest.mark.parametrize("piece", [1, 3, 5, 4096])
def test_metaex_inflate_stream_reads_stored_blocks_split_anywhere(piece):
    """IDAT data comes in pieces that can split a block header anywhere."""
    data = payload(MAX_STORED_BLOCK + 500)
    stream = stored_zlib(data, block_size=1000) + b"after"
    parts = [stream[i : i + piece] for i in range(0, len(stream), piece)]
    out = []
    result = inflate_stream(parts, len(data) + 10, out.append)
    assert b"".join(out) == data
    assert result.complete and not result.more and result.unused == len(b"after")


# -- PNG helpers --------------------------------------------------------------------------


def test_png_helpers_use_zlib_compress_outside_the_block():
    raw = png.raw_image(16, 16)
    assert png.deflate(raw, 9) == zlib.compress(raw, 9)
    with png.stored_deflate():
        assert png.deflate(raw, 9) == stored_zlib(raw)
        with png.stored_deflate():
            pass
        assert png.deflate(raw) == stored_zlib(raw)  # still on after a nested block
    assert png.deflate(raw) == zlib.compress(raw)


def test_stored_deflate_is_switched_off_after_an_error():
    with pytest.raises(RuntimeError), png.stored_deflate():
        raise RuntimeError
    assert png.deflate(b"abc") == zlib.compress(b"abc")


def rgb_pixels(raw: bytes, width: int) -> bytes:
    """Pixel bytes of non-interlaced 8-bit RGB scanlines that all use filter 0."""
    row = 1 + 3 * width
    assert len(raw) % row == 0
    rows = [raw[i : i + row] for i in range(0, len(raw), row)]
    assert all(r[0] == 0 for r in rows)
    return b"".join(r[1:] for r in rows)


WIDTH, HEIGHT = 12, 8
FRAMES = 3


def every_compressed_chunk() -> bytes:
    """An APNG with stored blocks in IDAT (split over 3 chunks), fdAT, zTXt,
    a compressed iTXt and iCCP."""
    with png.stored_deflate():
        data = png.apng(png.ztxt("Comment", "a compressed comment"),
                        png.itxt("Title", "Vacances", language="fr", translated="Titre",
                                 compress=True),
                        frames=FRAMES, width=WIDTH, height=HEIGHT)
        iccp = png.iccp(icc_profile(), "Display")
    # put iCCP before acTL (right after IHDR) and split IDAT in 3
    ihdr_end = 8 + 25
    data = data[:ihdr_end] + iccp + data[ihdr_end:]
    i = data.index(b"IDAT") - 4
    size = struct.unpack(">I", data[i : i + 4])[0]
    stream = data[i + 8 : i + 8 + size]
    parts = [stream[:7], stream[7:20], stream[20:]]
    return data[:i] + b"".join(png.chunk("IDAT", p) for p in parts) + data[i + 12 + size:]


def stored_streams(data: bytes) -> dict[str, list[bytes]]:
    """The zlib streams in each compressed chunk type."""
    out: dict[str, list[bytes]] = {}
    pos = 8
    idat = b""
    while pos < len(data):
        size = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8].decode("ascii")
        body = data[pos + 8 : pos + 8 + size]
        pos += 12 + size
        if kind == "IDAT":
            idat += body
        elif kind == "fdAT":
            out.setdefault(kind, []).append(body[4:])
        elif kind == "zTXt":
            out.setdefault(kind, []).append(body.split(b"\x00", 1)[1][1:])
        elif kind == "iTXt":
            # keyword NUL, compression flag, method, language NUL, translated NUL, text
            rest = body.split(b"\x00", 1)[1]
            if rest[0] == 1:
                out.setdefault(kind, []).append(rest[2:].split(b"\x00", 2)[2])
        elif kind == "iCCP":
            out.setdefault(kind, []).append(body.split(b"\x00", 1)[1][1:])
    out["IDAT"] = [idat]
    return out


def test_sample_style_png_uses_stored_blocks_everywhere():
    streams = stored_streams(every_compressed_chunk())
    assert sorted(streams) == ["IDAT", "fdAT", "iCCP", "iTXt", "zTXt"]
    assert len(streams["fdAT"]) == FRAMES - 1
    for kind, found in streams.items():
        for stream in found:
            assert stream[:2] == ZLIB_HEADER, kind
            stored_blocks(stream)  # raises unless it is all stored blocks


def test_metaex_reads_every_stored_chunk():
    data = every_compressed_chunk()
    parsed = png_format.parse(data)
    values = [f.value for s in parsed["sections"] for f in s.fields]
    assert "a compressed comment" in values  # zTXt
    assert "Vacances" in values  # compressed iTXt
    icc = next(s for s in parsed["sections"] if s.key == "icc")
    assert any(f.value == "Test RGB profile" for f in icc.fields)  # iCCP
    assert not any("malformed" in w or "CRITICAL" in w or "invalid" in w
                   for w in parsed["warnings"]), parsed["warnings"]

    check = png_format.check_image_data(png_format.walk_chunks(data))
    assert not check.problems and not check.skipped
    assert [s.stream.chunk_type for s in check.streams] == ["IDAT"] + ["fdAT"] * (FRAMES - 1)
    for s in check.streams:  # IDAT and every fdAT decompressed to the exact size
        assert s.size == s.expected and s.after_end == 0 and not s.hidden


def test_strip_and_verify_handle_stored_blocks():
    data = every_compressed_chunk()
    cleaned = png_format.strip(data)
    result = verify(cleaned, data)
    assert result.clean and result.image_data_match
    assert b"Vacances" not in cleaned and b"compressed comment" not in cleaned


def test_pillow_decodes_stored_blocks_to_the_expected_pixels():
    frames = decode_frames(every_compressed_chunk(), mode="RGB")
    assert len(frames) == FRAMES
    expected = [png.raw_image(WIDTH, HEIGHT)] + [png.raw_image(WIDTH, HEIGHT, seed=10 + i)
                                                for i in range(1, FRAMES)]
    for frame, raw in zip(frames, expected, strict=True):
        assert frame.size == (WIDTH, HEIGHT)
        assert frame.pixels == rgb_pixels(raw, WIDTH)


def test_pillow_decodes_a_multi_block_idat():
    """An image larger than one stored block (65,535 bytes)."""
    width, height = 160, 150  # 150 rows of 481 bytes = 72,150 bytes
    with png.stored_deflate():
        data = png.make_png(width=width, height=height, idat_parts=4)
    assert len(stored_blocks(stored_streams(data)["IDAT"][0])) == 2
    (frame,) = decode_frames(data, mode="RGB")
    assert frame.pixels == rgb_pixels(png.raw_image(width, height), width)
    check = png_format.check_image_data(png_format.walk_chunks(data))
    assert check.streams[0].size == check.streams[0].expected
