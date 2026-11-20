"""JPEG: walk the marker segments and read the metadata they carry.

A JPEG file is a sequence of markers (0xFF followed by a code byte). Most
markers start a segment with a 2-byte big-endian length; a few stand alone.
After a Start-of-Scan (SOS) segment comes entropy-coded image data, which
has no length and ends at the next marker that is not byte stuffing (FF00)
or a restart marker (RST0-7).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from metaex.core import exif
from metaex.core.errors import MalformedFileError
from metaex.core.result import Section

MAX_SEGMENTS = 10_000

SOI = 0xD8
EOI = 0xD9
SOS = 0xDA
COM = 0xFE
APP0 = 0xE0
APP1 = 0xE1

# Markers that are not followed by a length field.
_STANDALONE = {0x01, *range(0xD0, 0xD8), SOI, EOI}
_NAMED = {
    0x01: "TEM", 0xC4: "DHT", 0xC8: "JPG", 0xCC: "DAC", SOI: "SOI", EOI: "EOI",
    SOS: "SOS", 0xDB: "DQT", 0xDC: "DNL", 0xDD: "DRI", 0xDE: "DHP", 0xDF: "EXP",
    COM: "COM",
}


def marker_name(marker: int) -> str:
    if marker in _NAMED:
        return _NAMED[marker]
    if 0xE0 <= marker <= 0xEF:
        return f"APP{marker - 0xE0}"
    if 0xD0 <= marker <= 0xD7:
        return f"RST{marker - 0xD0}"
    if 0xC0 <= marker <= 0xCF:
        return f"SOF{marker - 0xC0}"
    if 0xF0 <= marker <= 0xFD:
        return f"JPG{marker - 0xF0}"
    return f"0x{marker:02X}"


def is_app(marker: int) -> bool:
    return 0xE0 <= marker <= 0xEF


@dataclass
class Segment:
    marker: int
    offset: int
    """Offset of the segment's 0xFF marker byte in the file."""
    length: int
    """Value of the length field (counts its own 2 bytes); 0 for standalone markers."""
    payload: bytes = b""
    """Segment data after the length field."""
    identifier: str | None = None
    """For APPn segments: the leading ASCII identifier, e.g. "Exif"."""
    scan_length: int = 0
    """For SOS: number of entropy-coded bytes that follow the segment."""

    @property
    def name(self) -> str:
        return marker_name(self.marker)

    @property
    def payload_offset(self) -> int:
        """Offset of the payload in the file."""
        return self.offset + 4


@dataclass
class JpegStructure:
    segments: list[Segment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    end_of_image: int | None = None
    """Offset just past the EOI marker, or None if no EOI was found."""
    error: str | None = None
    """Set when walking stopped early because the data is malformed."""


def walk_segments(data: bytes) -> JpegStructure:
    """Walk all segments from SOI to EOI.

    Raises MalformedFileError only if the data doesn't start with SOI.
    Anything malformed later stops the walk; the segments found so far are
    kept and the problem is recorded in ``error`` and ``warnings``.
    """
    if data[:2] != b"\xff\xd8":
        raise MalformedFileError("not a JPEG file: no SOI marker at offset 0")
    result = JpegStructure(segments=[Segment(SOI, 0, 0)])
    n = len(data)
    pos = 2
    try:
        while True:
            if len(result.segments) >= MAX_SEGMENTS:
                raise MalformedFileError(f"more than {MAX_SEGMENTS} segments")
            if pos >= n:
                result.warnings.append(
                    "missing end-of-image (EOI) marker; the file may be truncated")
                break
            if data[pos] != 0xFF:
                raise MalformedFileError(
                    f"expected a marker at offset {pos}, found byte 0x{data[pos]:02X}")
            while pos < n and data[pos] == 0xFF:  # optional fill bytes
                pos += 1
            if pos >= n:
                raise MalformedFileError("file ends inside a marker")
            marker = data[pos]
            start = pos - 1
            pos += 1
            if marker == 0x00:
                raise MalformedFileError(f"invalid marker 0xFF00 at offset {start}")

            if marker in _STANDALONE:
                if marker == SOI:
                    raise MalformedFileError(f"unexpected second SOI marker at offset {start}")
                result.segments.append(Segment(marker, start, 0))
                if marker == EOI:
                    result.end_of_image = pos
                    break
                continue

            if pos + 2 > n:
                raise MalformedFileError(
                    f"{marker_name(marker)} segment at offset {start} has no length field")
            length = int.from_bytes(data[pos : pos + 2], "big")
            if length < 2:
                raise MalformedFileError(
                    f"{marker_name(marker)} segment at offset {start} has invalid length {length}")
            if pos + length > n:
                raise MalformedFileError(
                    f"{marker_name(marker)} segment at offset {start} is truncated: "
                    f"needs {length} bytes, {n - pos} available")
            payload = bytes(data[pos + 2 : pos + length])
            segment = Segment(marker, start, length, payload,
                              identifier=_app_identifier(payload) if is_app(marker) else None)
            result.segments.append(segment)
            pos += length

            if marker == SOS:
                scan_end = _skip_entropy_data(data, pos)
                segment.scan_length = scan_end - pos
                pos = scan_end
    except MalformedFileError as exc:
        result.error = str(exc)
        result.warnings.append(
            f"malformed JPEG: {exc}; data after this point was not examined")
    return result


def _skip_entropy_data(data: bytes, pos: int) -> int:
    """Return the offset of the marker that ends a scan (or len(data)).

    Inside entropy-coded data, FF00 is a stuffed 0xFF byte and RST0-7 are
    restart markers that continue the same scan.
    """
    n = len(data)
    i = pos
    while True:
        j = data.find(b"\xff", i)
        if j < 0 or j + 1 >= n:
            return n
        following = data[j + 1]
        if following == 0x00 or 0xD0 <= following <= 0xD7:
            i = j + 2
        elif following == 0xFF:  # fill byte before a marker
            i = j + 1
        else:
            return j


def _app_identifier(payload: bytes) -> str:
    """Leading printable ASCII of an APPn payload, up to a NUL (max 80 chars)."""
    chars = []
    for b in payload[:80]:
        if not 0x20 <= b < 0x7F:
            break
        chars.append(chr(b))
    return "".join(chars)


def exif_thumbnail(data: bytes) -> bytes | None:
    """The embedded EXIF (IFD1) thumbnail of a JPEG, or None."""
    structure = walk_segments(bytes(data))
    for seg in structure.segments:
        if seg.marker == APP1 and seg.payload.startswith(exif.EXIF_APP1_PREFIX):
            try:
                thumb = exif.exif_thumbnail(exif.parse_exif(seg.payload))
            except MalformedFileError:
                continue
            if thumb is not None:
                return thumb.data
    return None


_SOF_MARKERS = {m for m in range(0xC0, 0xD0)} - {0xC4, 0xC8, 0xCC}


def image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from the first frame header (SOFn), or None."""
    try:
        structure = walk_segments(bytes(data))
    except MalformedFileError:
        return None
    for seg in structure.segments:
        if seg.marker in _SOF_MARKERS and len(seg.payload) >= 5:
            height = int.from_bytes(seg.payload[1:3], "big")
            width = int.from_bytes(seg.payload[3:5], "big")
            return width, height
    return None


# -- report ------------------------------------------------------------------------


def structure_section(structure: JpegStructure) -> Section:
    section = Section("structure", "JPEG structure")
    for i, seg in enumerate(structure.segments):
        if seg.length == 0:
            value = f"offset {seg.offset}"
        else:
            value = f"offset {seg.offset}, {seg.length} bytes"
        if seg.identifier:
            value += f', identifier "{seg.identifier}"'
        if seg.marker == SOS:
            value += f", followed by {seg.scan_length} bytes of image data"
        section.add(f"segment_{i}", seg.name, value)
    return section


def parse(data: bytes | bytearray | memoryview) -> dict:
    """Parse a JPEG and return its warnings, report sections and structure.

    Raises MalformedFileError if the data is not a JPEG at all; problems
    inside the file become warnings.
    """
    data = bytes(data)
    structure = walk_segments(data)
    return {
        "warnings": list(structure.warnings),
        "sections": [],
        "structure": structure_section(structure),
    }
