"""JPEG: walk the marker segments and read the metadata they carry.

A JPEG file is a sequence of markers (0xFF followed by a code byte). Most
markers start a segment with a 2-byte big-endian length; a few stand alone.
After a Start-of-Scan (SOS) segment comes entropy-coded image data, which
has no length and ends at the next marker that is not byte stuffing (FF00)
or a restart marker (RST0-7).
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

from metaex.core import exif, filetype, icc, iptc, xmp
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


# -- EXIF ------------------------------------------------------------------------


def exif_sections(structure: JpegStructure, warnings: list[str]) -> list[Section]:
    """EXIF, GPS and Thumbnail sections for each APP1 "Exif" segment."""
    segments = [s for s in structure.segments
                if s.marker == APP1 and s.payload.startswith(exif.EXIF_APP1_PREFIX)]
    out: list[Section] = []
    for i, seg in enumerate(segments):
        suffix = "" if i == 0 else f" (segment {i + 1})"
        try:
            st = exif.parse_exif(seg.payload)
        except MalformedFileError as exc:
            warnings.append(f"EXIF{suffix} not parsed: {exc}")
            section = Section("exif", "EXIF" + suffix)
            section.add("status", "EXIF", "present but not parsed", sensitive=True, note=str(exc))
            out.append(section)
            continue
        warnings.extend(f"EXIF{suffix}: {w}" for w in st.warnings)
        sections = exif.exif_sections(st, warnings, image_size=image_size)
        for section in sections:
            section.title += suffix
        out += sections
    return out


# -- XMP -------------------------------------------------------------------------

XMP_ID = b"http://ns.adobe.com/xap/1.0/\x00"
XMP_EXTENSION_ID = b"http://ns.adobe.com/xmp/extension/\x00"


def xmp_sections(structure: JpegStructure, warnings: list[str]) -> list[Section]:
    """Standard XMP packets plus reassembled Extended XMP."""
    standard = [s for s in structure.segments
                if s.marker == APP1 and s.payload.startswith(XMP_ID)]
    extended = [s for s in structure.segments
                if s.marker == APP1 and s.payload.startswith(XMP_EXTENSION_ID)]
    if not standard and not extended:
        return []
    section = Section("xmp", "XMP")
    for i, seg in enumerate(standard):
        label = "XMP packet" if i == 0 else f"XMP packet {i + 1}"
        _add_xmp(section, seg.payload[len(XMP_ID):], label, "", warnings)
    for guid, packet in assemble_extended_xmp(extended, warnings).items():
        _add_xmp(section, packet, f"Extended XMP {guid}", "(extended) ", warnings)
    return [section]


def _add_xmp(section: Section, packet: bytes, label: str, prefix: str,
             warnings: list[str]) -> None:
    section.add(f"{label}.size", label, f"{len(packet)} bytes")
    try:
        result = xmp.parse_xmp(packet)
    except MalformedFileError as exc:
        warnings.append(f"{label} not parsed: {exc}")
        section.add(f"{label}.status", f"{label} status", "present but not parsed",
                    sensitive=True, note=str(exc))
        return
    warnings.extend(result.warnings)
    for prop in result.properties:
        section.add(prefix + prop.path, prefix + prop.path, prop.value,
                    sensitive=prop.sensitive)


def assemble_extended_xmp(segments: list[Segment], warnings: list[str]) -> dict[str, bytes]:
    """Join Extended XMP chunks by GUID. Incomplete or inconsistent ones are skipped.

    Each chunk is: 32-byte GUID, 4-byte full length, 4-byte offset, data.
    """
    groups: dict[str, tuple[int, list[tuple[int, bytes]]]] = {}
    for seg in segments:
        body = seg.payload[len(XMP_EXTENSION_ID):]
        if len(body) < 40:
            warnings.append(f"Extended XMP chunk at offset {seg.offset} is too short; ignored")
            continue
        guid = body[:32].decode("ascii", errors="replace")
        full_length = int.from_bytes(body[32:36], "big")
        offset = int.from_bytes(body[36:40], "big")
        total, chunks = groups.setdefault(guid, (full_length, []))
        if full_length != total:
            warnings.append(f"Extended XMP {guid}: chunks disagree on total length; "
                            "chunk ignored")
            continue
        chunks.append((offset, body[40:]))

    packets: dict[str, bytes] = {}
    for guid, (total, chunks) in groups.items():
        if total > xmp.MAX_XMP_SIZE:
            warnings.append(f"Extended XMP {guid} claims {total} bytes; too large, not parsed")
            continue
        expected = 0
        parts = []
        for offset, chunk in sorted(chunks, key=lambda c: c[0]):
            if offset != expected:
                break
            parts.append(chunk)
            expected += len(chunk)
        if expected != total:
            warnings.append(f"Extended XMP {guid} is incomplete or has overlapping chunks; "
                            "not parsed")
            continue
        packets[guid] = b"".join(parts)
    return packets


# -- Photoshop resources and IPTC ------------------------------------------------------

PHOTOSHOP_ID = b"Photoshop 3.0\x00"
APP13 = 0xED


def photoshop_sections(structure: JpegStructure, warnings: list[str]) -> list[Section]:
    """APP13 "Photoshop 3.0" image resources, with IPTC-IIM decoded.

    A resource block may continue across several APP13 segments, so their
    payloads are joined before walking.
    """
    payloads = [s.payload[len(PHOTOSHOP_ID):] for s in structure.segments
                if s.marker == APP13 and s.payload.startswith(PHOTOSHOP_ID)]
    if not payloads:
        return []
    resources = iptc.parse_image_resources(b"".join(payloads), warnings)
    sections = [iptc.resources_section(resources, warnings)]
    datasets = []
    for res in resources:
        if res.id == iptc.IPTC_NAA:
            datasets.extend(iptc.parse_iim(res.data, warnings))
    if datasets:
        sections.insert(0, iptc.iptc_section(datasets))
    return sections


# -- Comments, ICC, MPF, JFIF, Adobe, other APPn ---------------------------------------

APP2 = 0xE2
APP14 = 0xEE
ICC_ID = b"ICC_PROFILE\x00"
MPF_ID = b"MPF\x00"
MAX_MPF_IMAGES = 100

MPF_TYPES = {
    0x030000: "Baseline MP primary image",
    0x010001: "Large thumbnail (VGA equivalent)",
    0x010002: "Large thumbnail (full HD equivalent)",
    0x020001: "Multi-frame image (panorama)",
    0x020002: "Multi-frame image (disparity)",
    0x020003: "Multi-frame image (multi-angle)",
    0x000000: "Undefined",
}


def _decode_text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def comment_sections(structure: JpegStructure) -> list[Section]:
    comments = [s for s in structure.segments if s.marker == COM]
    if not comments:
        return []
    section = Section("comments", "Comments")
    for i, seg in enumerate(comments, 1):
        text = _decode_text(seg.payload).rstrip("\x00").strip()
        section.add(f"comment_{i}", f"Comment {i}", text or "(empty)", sensitive=True)
    return [section]


def icc_sections(structure: JpegStructure, warnings: list[str]) -> list[Section]:
    profile = assemble_icc_profile(structure, warnings)
    if profile is None:
        return []
    try:
        return [icc.icc_section(profile, warnings)]
    except MalformedFileError as exc:
        warnings.append(f"ICC profile not parsed: {exc}")
        return []


def assemble_icc_profile(structure: JpegStructure, warnings: list[str]) -> bytes | None:
    """Reassemble a multi-chunk ICC profile (sequence number, chunk count, data).

    None if there is no profile or its chunks are missing or inconsistent.
    """
    chunks = [s for s in structure.segments if s.marker == APP2 and s.payload.startswith(ICC_ID)]
    if not chunks:
        return None
    parts: dict[int, bytes] = {}
    totals = set()
    for seg in chunks:
        body = seg.payload[len(ICC_ID):]
        if len(body) < 2:
            warnings.append(f"ICC profile chunk at offset {seg.offset} is too short")
            continue
        seq, total = body[0], body[1]
        totals.add(total)
        if seq == 0 or seq > total or seq in parts:
            warnings.append(f"ICC profile chunk at offset {seg.offset} has invalid or "
                            f"duplicate sequence number {seq} of {total}")
            continue
        parts[seq] = body[2:]
    if len(totals) != 1 or sorted(parts) != list(range(1, next(iter(totals)) + 1)):
        warnings.append("ICC profile chunks are missing or inconsistent; profile not parsed")
        return None
    return b"".join(parts[i] for i in sorted(parts))


def mpf_sections(structure: JpegStructure, data: bytes, warnings: list[str]) -> list[Section]:
    """Multi-Picture Format: extra images (large previews, stereo/disparity
    views, panorama frames) stored after the primary image's EOI."""
    segs = [s for s in structure.segments if s.marker == APP2 and s.payload.startswith(MPF_ID)]
    if not segs:
        return []
    seg = segs[0]
    header_offset = seg.payload_offset + len(MPF_ID)  # MPF offsets are relative to this
    try:
        mp = exif.parse_tiff(seg.payload[len(MPF_ID):], sub_ifd_tags={})
    except MalformedFileError as exc:
        warnings.append(f"MPF data not parsed: {exc}")
        return []
    warnings.extend(f"MPF: {w}" for w in mp.warnings)
    index = mp.ifd("IFD0")
    section = Section("mpf", "MPF (multi-picture)")
    count = index.value(0xB001) if index else None
    entries = index.get(0xB002) if index else None
    if not isinstance(count, int) or entries is None or not isinstance(entries.value, bytes):
        warnings.append("MPF index is incomplete; images not listed")
        section.add("status", "MPF", "present, index incomplete", sensitive=True)
        return [section]
    section.add("images", "Number of images", count)
    if count > MAX_MPF_IMAGES or len(entries.value) < 16 * count:
        warnings.append(f"MPF index claims {count} images but has room for "
                        f"{len(entries.value) // 16}; only those are listed")
    n = min(count, MAX_MPF_IMAGES, len(entries.value) // 16)
    order = "<" if mp.header.byte_order == "little" else ">"
    extra = []
    for i in range(n):
        attr, size, offset, _dep1, _dep2 = struct.unpack_from(order + "IIIHH", entries.value, 16 * i)
        kind = MPF_TYPES.get(attr & 0x00FFFFFF, f"type 0x{attr & 0x00FFFFFF:06X}")
        if i == 0:
            section.add("image_1", "Image 1", f"{kind}, {size} bytes (the main image)")
            continue
        start = header_offset + offset
        if offset == 0 or start + size > len(data):
            where, note = f"offset {start}", "outside the file"
        else:
            looks = "JPEG" if data[start : start + 2] == b"\xff\xd8" else "unrecognized data"
            where, note = f"offset {start}", looks
        section.add(f"image_{i + 1}", f"Image {i + 1}", f"{kind}, {size} bytes at {where}",
                    sensitive=True, note=note)
        extra.append(kind)
    if extra:
        warnings.append(f"CRITICAL: {len(extra)} extra image(s) hidden in the file (MPF): "
                        + "; ".join(extra))
    return [section]


def app0_sections(structure: JpegStructure, warnings: list[str]) -> list[Section]:
    section = Section("jfif", "JFIF")
    for seg in structure.segments:
        if seg.marker != APP0:
            continue
        p = seg.payload
        if p.startswith(b"JFIF\x00"):
            if len(p) < 14:
                warnings.append(f"JFIF segment at offset {seg.offset} is too short")
                continue
            units = {0: "(aspect ratio only)", 1: "dpi", 2: "dots/cm"}.get(p[7], f"unit {p[7]}")
            x, y = int.from_bytes(p[8:10], "big"), int.from_bytes(p[10:12], "big")
            section.add("version", "Version", f"{p[5]}.{p[6]:02d}")
            section.add("density", "Density", f"{x} x {y} {units}")
            if p[12] and p[13]:
                section.add("thumbnail", "JFIF thumbnail",
                            f"{p[12]} x {p[13]} pixels, uncompressed RGB", sensitive=True)
                warnings.append(f"JFIF {exif.THUMBNAIL_WARNING}")
        elif p.startswith(b"JFXX\x00"):
            kinds = {0x10: "JPEG", 0x11: "1-byte palette", 0x13: "RGB"}
            kind = kinds.get(p[5], "unknown format") if len(p) > 5 else "unknown format"
            section.add("jfxx_thumbnail", "JFXX thumbnail", f"{kind}, {len(p) - 6} bytes",
                        sensitive=True)
            warnings.append(f"JFXX {exif.THUMBNAIL_WARNING}")
    return [section] if section.fields else []


def adobe_sections(structure: JpegStructure) -> list[Section]:
    for seg in structure.segments:
        p = seg.payload
        if seg.marker == APP14 and p.startswith(b"Adobe") and len(p) >= 12:
            section = Section("adobe", "Adobe")
            section.add("version", "DCT encode version", int.from_bytes(p[5:7], "big"))
            transforms = {0: "Unknown (RGB or CMYK)", 1: "YCbCr", 2: "YCCK"}
            section.add("transform", "Color transform", transforms.get(p[11], str(p[11])))
            return [section]
    return []


def _is_known_app(seg: Segment) -> bool:
    p = seg.payload
    return (
        (seg.marker == APP0 and p.startswith((b"JFIF\x00", b"JFXX\x00")))
        or (seg.marker == APP1 and p.startswith((exif.EXIF_APP1_PREFIX, XMP_ID, XMP_EXTENSION_ID)))
        or (seg.marker == APP2 and p.startswith((ICC_ID, MPF_ID)))
        or (seg.marker == APP13 and p.startswith(PHOTOSHOP_ID))
        or (seg.marker == APP14 and p.startswith(b"Adobe"))
    )


def other_app_sections(structure: JpegStructure) -> list[Section]:
    section = Section("other_app", "Other APP segments")
    for seg in structure.segments:
        if is_app(seg.marker) and not _is_known_app(seg):
            ident = f' "{seg.identifier}"' if seg.identifier else " (no identifier)"
            section.add(f"{seg.name}@{seg.offset}", f"{seg.name}{ident}",
                        f"{len(seg.payload)} bytes at offset {seg.offset}",
                        sensitive=True, note="contents not examined")
    return [section] if section.fields else []


# -- Data after EOI ---------------------------------------------------------------


def trailing_sections(structure: JpegStructure, data: bytes, warnings: list[str],
                      has_mpf: bool = False) -> list[Section]:
    """Bytes after the end-of-image marker, which viewers ignore.

    Phones append whole files here: "Motion Photo" MP4 videos, extra JPEGs
    (MPF previews, depth maps), sometimes ZIPs or other data.
    """
    if structure.end_of_image is None:
        return []
    trailing = data[structure.end_of_image:]
    if not trailing:
        return []
    section = Section("trailing", "Trailing data")
    section.add("size", "Size", f"{len(trailing)} bytes", sensitive=True)
    section.add("offset", "Starts at offset", structure.end_of_image)
    if trailing.strip(b"\x00") == b"" or trailing.strip(b"\xff") == b"":
        section.add("content", "Content", "padding (only 0x00 or 0xFF bytes)")
        warnings.append(f"{len(trailing)} bytes of padding after the end of the image")
        return [section]
    detected = filetype.detect(trailing)
    content = detected.name if detected.key != filetype.UNKNOWN.key else "unrecognized data"
    section.add("content", "Content", content, sensitive=True,
                note="detected from the first bytes; more may follow")
    if has_mpf:
        section.add("mpf", "MPF", "includes the extra images listed in the MPF section")
    warnings.append(f"CRITICAL: {len(trailing)} bytes after the end of the image "
                    f"({content}); this can hide a whole video or file")
    return [section]


# -- Stripping ---------------------------------------------------------------------

# Report sections (and field keys; None = every field) that a stripped JPEG
# may still contain. Anything else found by verify is a leftover.
KEPT_BY_DESIGN: dict[str, set[str] | None] = {
    "jfif": {"version", "density"},
    "exif": {"byte_order", "Orientation"},
    "icc": None,
    "adobe": None,
}

DHT, DAC, DQT, DNL, DRI = 0xC4, 0xCC, 0xDB, 0xDC, 0xDD
# Segments that define the image. They are copied byte-for-byte, never
# re-encoded: frame headers, tables, restart interval, scan headers.
IMAGE_MARKERS = _SOF_MARKERS | {DHT, DAC, DQT, DNL, DRI, SOS}
# Structural markers metaex does not support (hierarchical and extension
# JPEG, reserved codes). Dropping them could break the image, so such files
# are refused.
_UNSUPPORTED_MARKERS = {0xC8, 0xDE, 0xDF} | set(range(0xF0, 0xFE)) | set(range(0x02, 0xC0))
JFIF_ID = b"JFIF\x00"
ADOBE_ID = b"Adobe"


def _make_segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


def _segment_bytes(data: bytes, seg: Segment) -> bytes:
    """The segment as stored: marker, length and payload."""
    return data[seg.offset : seg.offset + 2 + seg.length]


def _scan_bytes(data: bytes, seg: Segment) -> bytes:
    """The entropy-coded data after an SOS segment (including RSTn markers)."""
    start = seg.offset + 2 + seg.length
    return data[start : start + seg.scan_length]


def checked_structure(data: bytes) -> JpegStructure:
    """Walk a JPEG for stripping or verification: anything malformed raises.

    Unlike parse(), which reports what it can of a damaged file, this
    refuses truncated files, files without EOI, frame or scan, and
    unsupported structural markers.
    """
    structure = walk_segments(data)
    if structure.error:
        raise MalformedFileError(structure.error)
    if structure.end_of_image is None:
        raise MalformedFileError("missing end-of-image (EOI) marker; the file may be truncated")
    for seg in structure.segments:
        if seg.marker in _UNSUPPORTED_MARKERS:
            raise MalformedFileError(
                f"unsupported JPEG structure: {seg.name} marker at offset {seg.offset}")
    markers = {seg.marker for seg in structure.segments}
    if not markers & _SOF_MARKERS:
        raise MalformedFileError("no frame header (SOF) found")
    if SOS not in markers:
        raise MalformedFileError("no image data (SOS) found")
    return structure


def image_data(data: bytes | bytearray | memoryview) -> bytes:
    """The image-defining bytes (tables, SOF, SOS headers and scan data) in file order.

    Two files with the same image data decode to the same picture.
    Raises MalformedFileError like checked_structure().
    """
    data = bytes(data)
    structure = checked_structure(data)
    return b"".join(
        _segment_bytes(data, seg) + (_scan_bytes(data, seg) if seg.marker == SOS else b"")
        for seg in structure.segments
        if seg.marker in IMAGE_MARKERS
    )


def _first_app(structure: JpegStructure, marker: int, prefix: bytes) -> Segment | None:
    for seg in structure.segments:
        if seg.marker == marker and seg.payload.startswith(prefix):
            return seg
    return None


def _minimal_jfif(seg: Segment, paranoid: bool = False) -> bytes:
    """A new JFIF APP0 with the original version and density, without thumbnail.

    In paranoid mode the density is replaced by a plain 1:1 aspect ratio.
    """
    p = seg.payload
    if len(p) < 14:
        raise MalformedFileError(f"JFIF segment at offset {seg.offset} is too short")
    units = p[7]
    x_density, y_density = int.from_bytes(p[8:10], "big"), int.from_bytes(p[10:12], "big")
    if paranoid or units not in (0, 1, 2) or x_density == 0 or y_density == 0:
        units, x_density, y_density = 0, 1, 1  # plain 1:1 aspect
    payload = (JFIF_ID + bytes([p[5], p[6], units]) + x_density.to_bytes(2, "big")
               + y_density.to_bytes(2, "big") + b"\x00\x00")
    return _make_segment(APP0, payload)


def _minimal_adobe(seg: Segment) -> bytes:
    """A new APP14 "Adobe" with only its 7 defined bytes (version, flags, transform).

    Needed to decode CMYK/YCCK images correctly; contains no personal data.
    """
    p = seg.payload
    if len(p) < 12:
        raise MalformedFileError(f"Adobe APP14 segment at offset {seg.offset} is too short")
    return _make_segment(APP14, ADOBE_ID + p[5:12])


ORIENTATION_TAG = 0x0112


def original_orientation(structure: JpegStructure) -> int | None:
    """EXIF Orientation (1-8) from the first EXIF segment, or None."""
    seg = _first_app(structure, APP1, exif.EXIF_APP1_PREFIX)
    if seg is None:
        return None
    try:
        ifd0 = exif.parse_exif(seg.payload).ifd("IFD0")
    except MalformedFileError:
        return None
    value = ifd0.value(ORIENTATION_TAG) if ifd0 is not None else None
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 8:
        return value
    return None


def minimal_orientation_exif(orientation: int) -> bytes:
    """A new EXIF APP1 containing only IFD0 with the Orientation tag."""
    tiff = (b"MM\x00\x2a" + (8).to_bytes(4, "big")
            + (1).to_bytes(2, "big")
            + struct.pack(">HHIHH", ORIENTATION_TAG, exif.SHORT, 1, orientation, 0)
            + (0).to_bytes(4, "big"))
    return _make_segment(APP1, b"Exif\x00\x00" + tiff)


ICC_CHUNK_DATA = 65519  # 65533-byte APP2 payload minus "ICC_PROFILE\0" + 2 bytes


def icc_profile_segments(profile: bytes) -> bytes:
    """APP2 ICC_PROFILE segments carrying ``profile``, written fresh."""
    chunks = [profile[i : i + ICC_CHUNK_DATA] for i in range(0, len(profile), ICC_CHUNK_DATA)]
    if not 1 <= len(chunks) <= 255:
        raise MalformedFileError("ICC profile is too large to store in a JPEG")
    return b"".join(_make_segment(APP2, ICC_ID + bytes([i + 1, len(chunks)]) + chunk)
                    for i, chunk in enumerate(chunks))


def _usable_icc_profile(structure: JpegStructure) -> bytes | None:
    """The original ICC profile if it is complete and looks like a real profile."""
    profile = assemble_icc_profile(structure, [])
    if profile is None or len(profile) < 128 or profile[36:40] != b"acsp":
        return None
    if len(profile) > ICC_CHUNK_DATA * 255:
        return None
    return profile


def strip(data: bytes | bytearray | memoryview, *, keep_orientation: bool = True,
          paranoid: bool = False) -> bytes:
    """Return a copy of the JPEG with all metadata removed (whitelist).

    Kept: SOI, frame headers (SOFn), DHT, DAC, DQT, DRI, DNL, SOS with its
    scan data, EOI, all copied byte-for-byte; a newly written minimal JFIF
    APP0 (if the original had one) and APP14 Adobe. If the original EXIF
    Orientation is 2-8 (the image is displayed rotated or mirrored), a new
    minimal EXIF with only that tag is written unless ``keep_orientation``
    is False. The ICC color profile is kept, because removing it can shift
    colors, unless ``paranoid`` is True; paranoid mode also replaces the
    JFIF density with a 1:1 aspect ratio. Everything else is dropped: every
    other APPn segment, comments, and any data after EOI.

    Raises MalformedFileError for malformed, truncated or unsupported
    input; nothing is ever partly stripped.
    """
    data = bytes(data)
    structure = checked_structure(data)
    out = bytearray(b"\xff\xd8")
    jfif = _first_app(structure, APP0, JFIF_ID)
    if jfif is not None:
        out += _minimal_jfif(jfif, paranoid=paranoid)
    orientation = original_orientation(structure)
    if keep_orientation and orientation is not None and orientation != 1:
        out += minimal_orientation_exif(orientation)
    if not paranoid:
        profile = _usable_icc_profile(structure)
        if profile is not None:
            out += icc_profile_segments(profile)
    adobe = _first_app(structure, APP14, ADOBE_ID)
    if adobe is not None:
        out += _minimal_adobe(adobe)
    image = bytearray()
    for seg in structure.segments:
        if seg.marker in IMAGE_MARKERS:
            chunk = _segment_bytes(data, seg)
            if seg.marker == SOS:
                chunk += _scan_bytes(data, seg)
            out += chunk
            image += chunk
    out += b"\xff\xd9"
    cleaned = bytes(out)
    # Self-check: the output must be a well-formed JPEG with identical image data.
    if image_data(cleaned) != bytes(image):
        raise MalformedFileError("internal check failed: image data changed while stripping")
    return cleaned


# -- Verification -------------------------------------------------------------------

MINIMAL_EXIF_TIFF_SIZE = 8 + 2 + 12 + 4  # header, one-entry IFD0, next-IFD offset


def _is_orientation_only_exif(payload: bytes) -> bool:
    """EXIF holding only IFD0 with the Orientation tag, and no unused bytes."""
    try:
        st = exif.parse_exif(payload)
    except MalformedFileError:
        return False
    tiff = exif.tiff_from_exif_payload(payload)
    if [ifd.name for ifd in st.ifds] != ["IFD0"] or st.warnings:
        return False
    ifd0 = st.ifds[0]
    return ([e.tag for e in ifd0.entries] == [ORIENTATION_TAG]
            and ifd0.next_offset == 0
            and st.header.ifd0_offset == 8
            and len(tiff) == MINIMAL_EXIF_TIFF_SIZE)


def verify_details(cleaned: bytes, original: bytes | None = None) -> dict:
    """JPEG-specific verification, beyond the field-level whitelist.

    - Segment whitelist: only image segments, SOI/EOI, one minimal JFIF,
      one Orientation-only EXIF, complete ICC chunks and one minimal Adobe
      segment may remain. Anything else (including extra bytes hidden in an
      allowed segment) is a leftover.
    - SHA-256 of the image-defining bytes (tables, SOF, SOS and scan data);
      compared with the original's if one is given.
    """
    out: dict = {"leftovers": [], "problems": [], "image_sha256": None,
                 "original_image_sha256": None, "image_data_match": None}
    try:
        image = image_data(cleaned)
    except MalformedFileError as exc:
        out["problems"].append(f"not a complete JPEG: {exc}")
        return out
    out["image_sha256"] = hashlib.sha256(image).hexdigest()

    structure = walk_segments(bytes(cleaned))
    icc_ok = assemble_icc_profile(structure, []) is not None
    seen: set[str] = set()
    for seg in structure.segments:
        p = seg.payload
        if seg.marker in IMAGE_MARKERS or seg.marker in (SOI, EOI):
            continue
        if seg.marker == APP0 and p.startswith(JFIF_ID) and "jfif" not in seen:
            seen.add("jfif")
            if len(p) == 14 and p[12] == 0 and p[13] == 0:
                continue
            problem = f"{len(p) - 14} extra bytes in JFIF segment"
        elif seg.marker == APP1 and p.startswith(exif.EXIF_APP1_PREFIX) and "exif" not in seen:
            seen.add("exif")
            if _is_orientation_only_exif(p):
                continue
            problem = "EXIF contains more than the Orientation tag, or unused bytes"
        elif seg.marker == APP2 and p.startswith(ICC_ID):
            if icc_ok:
                continue
            problem = "ICC profile chunk that cannot be read as a complete profile"
        elif seg.marker == APP14 and p.startswith(ADOBE_ID) and "adobe" not in seen:
            seen.add("adobe")
            if len(p) == 12:
                continue
            problem = f"{len(p) - 12} extra bytes in Adobe segment"
        else:
            ident = f' "{seg.identifier}"' if seg.identifier else ""
            problem = f"{seg.name}{ident} segment is not allowed in a cleaned JPEG"
        out["leftovers"].append(("JPEG structure", f"{seg.name} at offset {seg.offset}",
                                 f"{problem} ({len(p)} bytes)"))

    if original is not None:
        try:
            original_image = image_data(original)
        except MalformedFileError as exc:
            out["problems"].append(f"original is not a complete JPEG ({exc}); "
                                   "image data not compared")
        else:
            out["original_image_sha256"] = hashlib.sha256(original_image).hexdigest()
            out["image_data_match"] = original_image == image
            if original_image != image:
                out["problems"].append("image data differs from the original")
    return out


def parse(data: bytes | bytearray | memoryview) -> dict:
    """Parse a JPEG and return its warnings, report sections and structure.

    Raises MalformedFileError if the data is not a JPEG at all; problems
    inside the file become warnings.
    """
    data = bytes(data)
    structure = walk_segments(data)
    warnings = list(structure.warnings)
    sections: list[Section] = []
    sections += exif_sections(structure, warnings)
    sections += xmp_sections(structure, warnings)
    sections += photoshop_sections(structure, warnings)
    sections += icc_sections(structure, warnings)
    mpf = mpf_sections(structure, data, warnings)
    sections += mpf
    sections += comment_sections(structure)
    sections += app0_sections(structure, warnings)
    sections += adobe_sections(structure)
    sections += other_app_sections(structure)
    sections += trailing_sections(structure, data, warnings, has_mpf=bool(mpf))
    return {
        "warnings": warnings,
        "sections": sections,
        "structure": structure_section(structure),
        "complete": structure.error is None,
    }
