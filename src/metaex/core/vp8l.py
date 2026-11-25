"""Find where a VP8L (WebP lossless) bitstream ends, without decoding the image.

A decoder stops reading once it has every pixel, so bytes after the end of
the bitstream (in the VP8L chunk, or in a losslessly compressed ALPH chunk)
are never shown and can hide data. Finding the end means reading what the
bitstream says, in order: the transforms, the prefix (Huffman) codes and
every coded symbol, including backward references. No pixels are built,
except for the small entropy image that chooses which prefix codes apply
where (its values are needed to read on). The format is described in RFC
9649 (WebP Image Format), section 3.

Every read is bounds-checked; the number of symbols read is capped by a
budget (``max_symbols``), so the work is bounded whatever the data says.
"""

from __future__ import annotations

from dataclasses import dataclass

from metaex.core.errors import MalformedFileError

SIGNATURE = 0x2F
MAX_CODE_LENGTH = 15
CODE_LENGTH_ORDER = (17, 18, 0, 1, 2, 3, 4, 5, 16, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15)
NUM_LENGTH_CODES = 24
NUM_DISTANCE_CODES = 40
CODE_TO_PLANE = bytes((
    0x18, 0x07, 0x17, 0x19, 0x28, 0x06, 0x27, 0x29, 0x16, 0x1a,
    0x26, 0x2a, 0x38, 0x05, 0x37, 0x39, 0x15, 0x1b, 0x36, 0x3a,
    0x25, 0x2b, 0x48, 0x04, 0x47, 0x49, 0x14, 0x1c, 0x35, 0x3b,
    0x46, 0x4a, 0x24, 0x2c, 0x58, 0x45, 0x4b, 0x34, 0x3c, 0x03,
    0x57, 0x59, 0x13, 0x1d, 0x56, 0x5a, 0x23, 0x2d, 0x44, 0x4c,
    0x55, 0x5b, 0x33, 0x3d, 0x68, 0x02, 0x67, 0x69, 0x12, 0x1e,
    0x66, 0x6a, 0x22, 0x2e, 0x54, 0x5c, 0x43, 0x4d, 0x65, 0x6b,
    0x32, 0x3e, 0x78, 0x01, 0x77, 0x79, 0x53, 0x5d, 0x11, 0x1f,
    0x64, 0x6c, 0x42, 0x4e, 0x76, 0x7a, 0x21, 0x2f, 0x75, 0x7b,
    0x31, 0x3f, 0x63, 0x6d, 0x52, 0x5e, 0x00, 0x74, 0x7c, 0x41,
    0x4f, 0x10, 0x20, 0x62, 0x6e, 0x30, 0x73, 0x7d, 0x51, 0x5f,
    0x40, 0x72, 0x7e, 0x61, 0x6f, 0x50, 0x71, 0x7f, 0x60, 0x70,
))
"""The 120 short distance codes: (y offset << 4) | (8 - x offset)."""
_FAST_BITS = 8
MAX_ENTROPY_PIXELS = 1 << 20
"""Largest entropy image (the image that chooses prefix codes by position)
read; encoders keep it to a few thousand pixels."""


class _OverBudget(Exception):
    pass


class _Bits:
    """Little-endian bit reader (LSB first), as VP8L packs its bits."""

    def __init__(self, data: bytes, start: int) -> None:
        self.data, self.pos, self.acc, self.n = data, start, 0, 0

    def _fill(self, k: int) -> None:
        data, n = self.data, len(self.data)
        while self.n < k and self.pos < n:
            self.acc |= data[self.pos] << self.n
            self.pos += 1
            self.n += 8

    def read(self, k: int) -> int:
        if self.n < k:
            self._fill(k)
            if self.n < k:
                raise MalformedFileError("the lossless bitstream is truncated")
        value = self.acc & ((1 << k) - 1)
        self.acc >>= k
        self.n -= k
        return value

    def peek(self, k: int) -> int:
        """The next k bits; past the end of the data they read as zero."""
        if self.n < k:
            self._fill(k)
        return self.acc & ((1 << k) - 1)

    def skip(self, k: int) -> None:
        if k > self.n:
            raise MalformedFileError("the lossless bitstream is truncated")
        self.acc >>= k
        self.n -= k

    @property
    def at(self) -> int:
        """Bits read so far (from the start of the data)."""
        return self.pos * 8 - self.n

    @property
    def end(self) -> int:
        """Offset just past the last byte any bit was read from."""
        return self.pos - self.n // 8


class _Code:
    """A prefix code: one symbol (no bits), or a lookup table for codes up to
    _FAST_BITS long and a dict for longer ones."""

    __slots__ = ("single", "table", "long", "max_length")

    def __init__(self, lengths: list[int]) -> None:
        used = [(length, symbol) for symbol, length in enumerate(lengths) if length]
        if not used:
            raise MalformedFileError("a prefix code has no symbols")
        self.single: int | None = used[0][1] if len(used) == 1 else None
        self.table: list = [None] * (1 << _FAST_BITS)
        self.long: dict[tuple[int, int], int] = {}
        self.max_length = max(length for length, _s in used)
        if self.single is not None:
            return
        if sum(1 << (MAX_CODE_LENGTH - length) for length, _s in used) != 1 << MAX_CODE_LENGTH:
            raise MalformedFileError("a prefix code is incomplete or oversubscribed")
        code = 0
        previous = 0
        for length, symbol in sorted(used):
            code <<= length - previous
            previous = length
            reversed_code = int(format(code, f"0{length}b")[::-1], 2)
            if length <= _FAST_BITS:
                for high in range(1 << (_FAST_BITS - length)):
                    self.table[reversed_code | (high << length)] = (symbol, length)
            else:
                self.long[(length, reversed_code)] = symbol
            code += 1

    def read(self, bits: _Bits) -> int:
        if self.single is not None:
            return self.single
        entry = self.table[bits.peek(_FAST_BITS)]
        if entry is not None:
            bits.skip(entry[1])
            return entry[0]
        value = bits.peek(self.max_length)
        for length in range(_FAST_BITS + 1, self.max_length + 1):
            symbol = self.long.get((length, value & ((1 << length) - 1)))
            if symbol is not None:
                bits.skip(length)
                return symbol
        raise MalformedFileError("invalid prefix code in the lossless bitstream")


def _read_code(bits: _Bits, alphabet: int) -> _Code:
    lengths = [0] * alphabet
    if bits.read(1):  # simple code: one or two symbols
        count = bits.read(1) + 1
        symbols = [bits.read(8 if bits.read(1) else 1)]
        if count == 2:
            symbols.append(bits.read(8))
        for symbol in symbols:
            if symbol >= alphabet:
                raise MalformedFileError("a prefix code uses a symbol outside its alphabet")
            lengths[symbol] = 1 if count == 2 and symbols[0] != symbols[1] else 0
        if count == 1 or symbols[0] == symbols[1]:
            lengths[symbols[0]] = 1  # a single symbol is read with no bits
        return _Code(lengths)
    code_lengths = [0] * len(CODE_LENGTH_ORDER)
    for i in range(bits.read(4) + 4):
        code_lengths[CODE_LENGTH_ORDER[i]] = bits.read(3)
    length_code = _Code(code_lengths)
    if bits.read(1):
        max_symbol = 2 + bits.read(2 + 2 * bits.read(3))
        if max_symbol > alphabet:
            raise MalformedFileError("a prefix code has more symbols than its alphabet")
    else:
        max_symbol = alphabet
    symbol, previous = 0, 8
    while symbol < alphabet and max_symbol > 0:
        max_symbol -= 1
        value = length_code.read(bits)
        if value < 16:
            lengths[symbol] = value
            symbol += 1
            if value:
                previous = value
            continue
        extra, base = {16: (2, 3), 17: (3, 3), 18: (7, 11)}[value]
        repeat = base + bits.read(extra)
        if symbol + repeat > alphabet:
            raise MalformedFileError("a prefix code repeats past the end of its alphabet")
        lengths[symbol:symbol + repeat] = [previous if value == 16 else 0] * repeat
        symbol += repeat
    return _Code(lengths)


def _prefix_value(bits: _Bits, symbol: int) -> int:
    """A length or distance from its prefix symbol and extra bits."""
    if symbol < 4:
        return symbol + 1
    extra = (symbol - 2) >> 1
    return ((2 + (symbol & 1)) << extra) + bits.read(extra) + 1


def _distance(width: int, code: int) -> int:
    if code > len(CODE_TO_PLANE):
        return code - len(CODE_TO_PLANE)
    plane = CODE_TO_PLANE[code - 1]
    return max(1, (plane >> 4) * width + 8 - (plane & 0xF))


def _divide_up(value: int, bits: int) -> int:
    return (value + (1 << bits) - 1) >> bits


class _Walker:
    def __init__(self, data: bytes, start: int, budget: int) -> None:
        self.bits = _Bits(data, start)
        self.budget = budget

    def image(self, width: int, height: int, top_level: bool,
              want_pixels: bool = False) -> list[int] | None:
        """Read one entropy-coded image; its ARGB values if ``want_pixels``."""
        bits = self.bits
        cache_bits = 0
        if bits.read(1):
            cache_bits = bits.read(4)
            if not 1 <= cache_bits <= 11:
                raise MalformedFileError(f"invalid color cache size ({cache_bits} bits)")
        cache_size = 1 << cache_bits if cache_bits else 0
        meta: list[int] | None = None
        prefix_bits = prefix_width = 0
        groups_count = 1
        if top_level and bits.read(1):
            prefix_bits = bits.read(3) + 2
            prefix_width = _divide_up(width, prefix_bits)
            prefix_height = _divide_up(height, prefix_bits)
            if prefix_width * prefix_height > MAX_ENTROPY_PIXELS:
                raise MalformedFileError("the entropy image of the lossless bitstream is "
                                         "larger than metaex reads")
            entropy = self.image(prefix_width, prefix_height, False, True)
            meta = [(p >> 8) & 0xFFFF for p in entropy]
            groups_count = max(meta) + 1
        alphabets = (256 + NUM_LENGTH_CODES + cache_size, 256, 256, 256, NUM_DISTANCE_CODES)
        groups = [[_read_code(bits, a) for a in alphabets] for _ in range(groups_count)]
        total = width * height
        # A group whose codes all have one symbol, a color, reads no bits at
        # all: its pixels are skipped in one step (a flat image can be all of them).
        free = [all(c.single is not None for c in g[:4]) and g[0].single < 256 for g in groups]
        stops: dict[int, list[int]] = {}

        def next_stop(row: int, block: int) -> int:
            """The first block from ``block`` on in this row of the entropy
            image whose group reads bits (prefix_width if none)."""
            if row not in stops:
                line = meta[row * prefix_width:(row + 1) * prefix_width]
                out, stop = [prefix_width] * prefix_width, prefix_width
                for b in range(prefix_width - 1, -1, -1):
                    if not free[line[b]]:
                        stop = b
                    out[b] = stop
                stops[row] = out
            return stops[row][block]

        pixels: list[int] | None = [] if want_pixels else None
        cache = [0] * cache_size if want_pixels and cache_size else None
        shift = 32 - cache_bits
        mask = (1 << prefix_bits) - 1
        x = y = pos = 0
        group = groups[0]
        refresh = meta is not None
        while pos < total:
            self.budget -= 1
            if self.budget < 0:
                raise _OverBudget
            if pixels is None and meta is None and free[0]:
                break  # every pixel left reads no bits
            if meta is not None and (refresh or not x & mask):
                index = meta[(y >> prefix_bits) * prefix_width + (x >> prefix_bits)]
                group = groups[index]
                refresh = False
                if pixels is None and free[index]:
                    stop = next_stop(y >> prefix_bits, x >> prefix_bits) << prefix_bits
                    skip = min(stop, width) - x
                    pos += skip
                    x += skip
                    if x >= width:
                        y, x = y + 1, 0
                    continue
            before = bits.at
            green = group[0].read(bits)
            if green < 256:
                red, blue, alpha = group[1].read(bits), group[2].read(bits), group[3].read(bits)
                count = 1
                if pixels is not None:
                    new = [(alpha << 24) | (red << 16) | (green << 8) | blue]
            elif green < 256 + NUM_LENGTH_CODES:
                count = _prefix_value(bits, green - 256)
                distance = _distance(width, _prefix_value(bits, group[4].read(bits)))
                if distance > pos or pos + count > total:
                    raise MalformedFileError("a backward reference in the lossless bitstream "
                                             "points outside the image")
                refresh = True
                if pixels is not None:
                    start = pos - distance
                    new = [pixels[start + i] if start + i < pos else 0 for i in range(count)]
                    for i in range(count):  # overlapping copies repeat what they copy
                        if start + i >= pos:
                            new[i] = new[i - distance]
            else:
                index = green - 256 - NUM_LENGTH_CODES
                if index >= cache_size:
                    raise MalformedFileError("invalid color cache index")
                count = 1
                if pixels is not None:
                    new = [cache[index]]
            if pixels is not None:
                pixels += new
                if cache is not None:
                    for p in new:
                        cache[((0x1E35A7BD * p) & 0xFFFFFFFF) >> shift] = p
            pos += count
            x += count
            if x >= width:
                y += x // width
                x %= width
            if pixels is None and meta is None and bits.at == before and pos < total:
                # It read no bits, so every symbol left is this same copy (a
                # color is handled above): it must fill the image exactly.
                if (total - pos) % count:
                    raise MalformedFileError("a backward reference in the lossless "
                                             "bitstream points outside the image")
                break
        return pixels

    def stream(self, width: int, height: int) -> None:
        """The transforms, then the image itself."""
        bits = self.bits
        seen: set[int] = set()
        xsize = width
        while bits.read(1):
            kind = bits.read(2)
            if kind in seen:
                raise MalformedFileError("a transform is used twice in the lossless bitstream")
            seen.add(kind)
            if kind in (0, 1):  # predictor, color: an image of per-block values
                size_bits = bits.read(3) + 2
                self.image(_divide_up(xsize, size_bits), _divide_up(height, size_bits), False)
            elif kind == 3:  # color indexing: a palette, pixels packed together
                colors = bits.read(8) + 1
                self.image(colors, 1, False)
                xsize = _divide_up(xsize, 3 if colors <= 2 else 2 if colors <= 4
                                   else 1 if colors <= 16 else 0)
        self.image(xsize, height, True)


@dataclass(frozen=True)
class StreamEnd:
    end: int | None
    """Offset just past the bitstream; None if it was too large to read."""
    symbols: int
    """Symbols read (what was used of the budget)."""


def stream_end(data: bytes, start: int, width: int, height: int,
               max_symbols: int) -> StreamEnd:
    """Where a VP8L image stream (transforms and image, as in a VP8L chunk
    after its header, or in a lossless ALPH chunk) that starts at ``start``
    ends. Raises MalformedFileError if it is invalid or truncated."""
    walker = _Walker(data, start, max_symbols)
    try:
        walker.stream(width, height)
    except _OverBudget:
        return StreamEnd(None, max_symbols)
    return StreamEnd(walker.bits.end, max_symbols - walker.budget)


def bitstream_end(data: bytes, max_symbols: int) -> StreamEnd:
    """Where the bitstream of a VP8L chunk's payload ``data`` ends."""
    if len(data) < 5 or data[0] != SIGNATURE:
        raise MalformedFileError("the lossless bitstream has no VP8L signature")
    header = int.from_bytes(data[1:5], "little")
    if header >> 29:
        raise MalformedFileError(f"unknown lossless bitstream version {header >> 29}")
    width, height = (header & 0x3FFF) + 1, ((header >> 14) & 0x3FFF) + 1
    return stream_end(data, 5, width, height, max_symbols)
