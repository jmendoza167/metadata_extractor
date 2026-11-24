"""WAV: walk the RIFF chunks and read the metadata they carry.

A WAV file is a RIFF container: "RIFF", a 4-byte little-endian size (the
file size minus 8), "WAVE", then chunks. Each chunk is a FourCC, a 4-byte
little-endian payload size, the payload and one padding byte if the size
is odd. The audio is described by "fmt " (format, channels, sample rate,
bits per sample, ...) and stored in "data"; "fact" holds the number of
samples of compressed formats.

Metadata chunks: LIST "INFO" (artist, comments, dates, software, ...),
LIST "adtl" (text labels of cue points: labl, note, ltxt), "bext" (the
Broadcast Wave extension: description, originator, dates, UMID, coding
history), "iXML" and "axml" (XML from field recorders and broadcast
systems), "_PMX" (XMP), "id3 " (an ID3v2 tag), "cue " (markers) and
"smpl" (loop points). JUNK, PAD and FLLR chunks reserve space and should
hold only zeros. Players ignore anything after the end the RIFF size
gives.

RF64 and BW64 (WAV files over 4 GiB, with 64-bit sizes) are not supported.
"""

from __future__ import annotations

import hashlib
import io
import wave
from dataclasses import dataclass, field

from metaex.core import id3, xmp
from metaex.core.embedded import Picture, picture_sections
from metaex.core.errors import HiddenDataError, MalformedFileError
from metaex.core.result import Section

MAX_CHUNKS = 100_000
MAX_XML_FIELDS = 1000
MAX_CUE_POINTS = 100_000
MAX_SHOWN_CHARS = 256 * 1024
BEXT_FIXED_SIZE = 602
PADDING_CHUNKS = ("JUNK", "junk", "PAD ", "FLLR")
LIST_TYPES = ("INFO", "adtl")

WAVE_FORMAT_PCM, WAVE_FORMAT_FLOAT, WAVE_FORMAT_EXTENSIBLE = 0x0001, 0x0003, 0xFFFE
FORMAT_NAMES = {
    0x0001: "PCM", 0x0002: "Microsoft ADPCM", 0x0003: "IEEE float", 0x0006: "A-law",
    0x0007: "mu-law", 0x0011: "IMA ADPCM", 0x0031: "GSM 6.10", 0x0050: "MPEG audio",
    0x0055: "MPEG Layer III", 0x0092: "Dolby AC-3 (S/PDIF)", 0x00FF: "AAC",
    0x2000: "Dolby AC-3", 0xFFFE: "extensible",
}
_GUID_TAIL = bytes.fromhex("000000001000800000aa00389b71")
"""Last 14 bytes of the KSDATAFORMAT_SUBTYPE GUIDs (their first 2 bytes
are the format code)."""

INFO_NAMES = {
    "IARL": "Archival location", "IART": "Artist", "ICMS": "Commissioned by",
    "ICMT": "Comments", "ICOP": "Copyright", "ICRD": "Creation date", "ICRP": "Cropped",
    "IDIM": "Dimensions", "IDPI": "Dots per inch", "IENG": "Engineer", "IGNR": "Genre",
    "IKEY": "Keywords", "ILGT": "Lightness", "IMED": "Medium", "INAM": "Title",
    "IPLT": "Palette", "IPRD": "Product (album)", "ISBJ": "Subject", "ISFT": "Software",
    "ISHP": "Sharpness", "ISRC": "Source", "ISRF": "Source form", "ITCH": "Technician",
    "ITRK": "Track number", "IPRT": "Part", "ILNG": "Language",
}
KNOWN_CHUNKS = {"fmt ", "fact", "data", "LIST", "bext", "iXML", "axml", "_PMX", "id3 ",
                "ID3 ", "cue ", "smpl", *PADDING_CHUNKS}
OTHER_CHUNK_NOTES = {
    "PEAK": "peak levels, with a time stamp", "cart": "broadcast cart chunk (title, artist, "
    "client, dates)", "inst": "instrument settings", "acid": "ACID loop settings",
    "plst": "playlist of cue points", "DISP": "display text or picture", "umid": "unique "
    "material identifier", "levl": "peak envelope", "chna": "channel assignments",
    "dbmd": "Dolby metadata", "minf": "Pro Tools data", "elm1": "Pro Tools data",
    "regn": "Pro Tools regions", "ds64": "64-bit sizes (RF64)",
}


@dataclass
class Chunk:
    fourcc: str
    offset: int
    """Offset of the chunk header in the file."""
    size: int
    """Payload size, as stored."""
    data: bytes
    padding: bytes = b""
    """The padding byte after an odd-sized payload (b"" if none)."""
    list_type: str | None = None
    """LIST chunks: the list type, e.g. "INFO"."""
    children: list[Chunk] = field(default_factory=list)
    """LIST INFO and LIST adtl: the sub-chunks."""

    @property
    def end(self) -> int:
        return self.offset + 8 + self.size + len(self.padding)

    @property
    def label(self) -> str:
        name = self.fourcc.strip() or repr(self.fourcc)
        return f"{name} {self.list_type}" if self.list_type is not None else name


@dataclass
class WavStructure:
    riff_size: int
    chunks: list[Chunk] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    end: int | None = None
    """Offset just past the RIFF data (8 + RIFF size), if the file is that long."""
    error: str | None = None
    """Set when walking stopped early because the data is malformed."""

    def first(self, fourcc: str) -> Chunk | None:
        return next((c for c in self.chunks if c.fourcc == fourcc), None)

    def all(self, *fourccs: str) -> list[Chunk]:
        return [c for c in self.chunks if c.fourcc in fourccs]

    def lists(self, list_type: str) -> list[Chunk]:
        return [c for c in self.chunks if c.fourcc == "LIST" and c.list_type == list_type]


def _fourcc(raw: bytes) -> str:
    return raw.decode("latin-1")


class _Walker:
    def __init__(self, data: bytes, result: WavStructure) -> None:
        self.data = data
        self.result = result
        self.count = 0

    def chunks(self, start: int, end: int, where: str, out: list[Chunk]) -> None:
        """Append the chunks between ``start`` and ``end`` to ``out`` (so the
        ones found before a problem are kept); raises MalformedFileError."""
        pos = start
        data = self.data
        while pos < end:
            # Every chunk moves pos forward by at least 8 bytes.
            self.count += 1
            if self.count > MAX_CHUNKS:
                raise MalformedFileError(f"more than {MAX_CHUNKS} chunks")
            if pos + 8 > end:
                raise MalformedFileError(f"chunk header at offset {pos} is truncated{where}")
            fourcc = _fourcc(data[pos:pos + 4])
            size = int.from_bytes(data[pos + 4:pos + 8], "little")
            if size > end - pos - 8:
                raise MalformedFileError(
                    f"{fourcc!r} chunk at offset {pos} is truncated{where}: needs {size} "
                    f"bytes, {end - pos - 8} available")
            padding = b""
            if size % 2:
                if pos + 8 + size < end:
                    padding = data[pos + 8 + size:pos + 9 + size]
                else:
                    self.result.warnings.append(
                        f"{fourcc!r} chunk at offset {pos} has no padding byte{where}")
            chunk = Chunk(fourcc, pos, size, data[pos + 8:pos + 8 + size], padding)
            out.append(chunk)
            if fourcc == "LIST" and not where:
                if size < 4:
                    raise MalformedFileError(f"LIST chunk at offset {pos} is too short")
                chunk.list_type = _fourcc(chunk.data[:4])
                if chunk.list_type in LIST_TYPES:
                    self.chunks(pos + 12, pos + 8 + size,
                                f" (in the LIST {chunk.list_type} chunk at offset {pos})",
                                chunk.children)
            pos = chunk.end


def walk_chunks(data: bytes | bytearray | memoryview) -> WavStructure:
    """Walk all chunks inside the RIFF container (and inside LIST INFO and
    LIST adtl chunks).

    Raises MalformedFileError only if the data doesn't start with a RIFF
    WAVE header. Anything malformed later stops the walk; the chunks found
    so far are kept and the problem is recorded in ``error`` and ``warnings``.
    """
    data = bytes(data)
    if len(data) < 12 or data[8:12] != b"WAVE" or data[:4] not in (b"RIFF", b"RF64", b"BW64"):
        raise MalformedFileError("not a WAV file: no RIFF WAVE header")
    if data[:4] != b"RIFF":
        raise MalformedFileError(f"{data[:4].decode('ascii')} files (WAV over 4 GiB) are not "
                                 "supported")
    riff_size = int.from_bytes(data[4:8], "little")
    result = WavStructure(riff_size)
    end = 8 + riff_size
    walker = _Walker(data, result)
    try:
        if riff_size < 4:
            raise MalformedFileError(f"RIFF size {riff_size} is too small")
        if end > len(data):
            result.error = (f"RIFF size says the file has {end} bytes, but it has "
                            f"{len(data)}; the file is truncated")
            walker.chunks(12, len(data), "", result.chunks)
        else:
            result.end = end
            walker.chunks(12, end, "", result.chunks)
    except MalformedFileError as exc:
        result.error = result.error or str(exc)
    if result.error:
        result.warnings.append(f"malformed WAV: {result.error}; data after this point was not "
                               "examined")
    return result


# -- the audio format ----------------------------------------------------------------------


@dataclass
class Format:
    tag: int
    channels: int
    sample_rate: int
    byte_rate: int
    block_align: int
    bits: int
    extension: bytes = b""
    """cbSize bytes after the 18-byte WAVEFORMATEX (format-specific)."""
    extra: bytes = b""
    """Bytes in the fmt chunk after the extension: not part of the format."""
    subformat: int | None = None
    """WAVE_FORMAT_EXTENSIBLE: the format code in the sub-format GUID."""
    valid_bits: int | None = None
    channel_mask: int | None = None

    @property
    def code(self) -> int:
        """The effective format code (the sub-format's, for extensible)."""
        return self.subformat if self.subformat is not None else self.tag

    @property
    def name(self) -> str:
        if self.tag == WAVE_FORMAT_EXTENSIBLE:
            sub = FORMAT_NAMES.get(self.subformat, f"0x{self.subformat:04x}") if (
                self.subformat is not None) else "unknown sub-format"
            return f"{sub} (extensible)"
        return FORMAT_NAMES.get(self.tag, f"format 0x{self.tag:04x}")


def parse_format(chunk: Chunk) -> Format:
    d = chunk.data
    if len(d) < 16:
        raise MalformedFileError(f"fmt chunk is too short ({len(d)} bytes)")
    fmt = Format(int.from_bytes(d[0:2], "little"), int.from_bytes(d[2:4], "little"),
                 int.from_bytes(d[4:8], "little"), int.from_bytes(d[8:12], "little"),
                 int.from_bytes(d[12:14], "little"), int.from_bytes(d[14:16], "little"))
    if len(d) >= 18:
        cb_size = int.from_bytes(d[16:18], "little")
        if 18 + cb_size > len(d):
            raise MalformedFileError(f"fmt chunk says {cb_size} extension bytes follow, but "
                                     f"only {len(d) - 18} do")
        fmt.extension, fmt.extra = d[18:18 + cb_size], d[18 + cb_size:]
    elif len(d) > 16:
        fmt.extra = d[16:]
    if fmt.tag == WAVE_FORMAT_EXTENSIBLE:
        ext = fmt.extension
        if len(ext) < 22:
            raise MalformedFileError("WAVE_FORMAT_EXTENSIBLE fmt chunk is too short")
        fmt.valid_bits = int.from_bytes(ext[0:2], "little")
        fmt.channel_mask = int.from_bytes(ext[2:6], "little")
        guid = ext[6:22]
        if guid[2:] == _GUID_TAIL:
            fmt.subformat = int.from_bytes(guid[0:2], "little")
    if fmt.channels == 0 or fmt.sample_rate == 0 or fmt.block_align == 0:
        raise MalformedFileError("fmt chunk has zero channels, sample rate or block size")
    return fmt


def format_section(structure: WavStructure, warnings: list[str]) -> list[Section]:
    chunk = structure.first("fmt ")
    if chunk is None:
        return []
    try:
        fmt = parse_format(chunk)
    except MalformedFileError as exc:
        warnings.append(f"malformed WAV: {exc}")
        return []
    section = Section("wav_format", "Audio")
    section.add("format", "Format", fmt.name)
    section.add("channels", "Channels", fmt.channels)
    section.add("sample_rate", "Sample rate", f"{fmt.sample_rate} Hz")
    if fmt.bits:
        section.add("bits", "Bits per sample", fmt.bits if fmt.valid_bits in (None, fmt.bits)
                    else f"{fmt.valid_bits} (in {fmt.bits})")
    section.add("byte_rate", "Byte rate", f"{fmt.byte_rate} bytes/s")
    if fmt.channel_mask:
        section.add("channel_mask", "Channel mask", f"0x{fmt.channel_mask:x}")
    data = structure.first("data")
    fact = structure.first("fact")
    if fact is not None and len(fact.data) >= 4 and fmt.code not in (1, 3):
        frames = int.from_bytes(fact.data[:4], "little")
    elif data is not None:
        frames = data.size // fmt.block_align
    else:
        frames = None
    if frames is not None:
        section.add("duration", "Duration", f"{frames / fmt.sample_rate:.3f} s")
    if data is not None:
        section.add("data_size", "Audio data", f"{data.size} bytes")
    out = [section]
    if fmt.extra and fmt.extra.count(0) != len(fmt.extra):
        warnings.append(f"CRITICAL: the fmt chunk holds {len(fmt.extra)} bytes after the "
                        "format description (not part of the format)")
        hidden = Section("hidden_chunk_data", "Hidden data in chunks")
        hidden.add("fmt_extra", "After the format description in the fmt chunk",
                   f"{len(fmt.extra)} bytes", sensitive=True)
        out.append(hidden)
    return out


# -- metadata chunks -------------------------------------------------------------------------


def _text(raw: bytes) -> str:
    """A RIFF string: up to the first 0x00; UTF-8 if valid, else Latin-1."""
    raw = raw.split(b"\x00", 1)[0]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    text = text.strip()
    if len(text) > MAX_SHOWN_CHARS:
        text = text[:MAX_SHOWN_CHARS] + f" [shortened: {len(text)} characters in total]"
    return text


def info_sections(structure: WavStructure) -> list[Section]:
    out = []
    lists = structure.lists("INFO")
    for n, chunk in enumerate(lists):
        title = "RIFF INFO" + (f" (list {n + 1})" if len(lists) > 1 else "")
        section = Section("wav_info", title)
        for child in chunk.children:
            section.add(child.fourcc, INFO_NAMES.get(child.fourcc, f"Field {child.label}"),
                        _text(child.data), sensitive=child.fourcc not in ("IGNR", "ITRK"))
        if not section.fields:
            section.add("empty", "Fields", "(none)")
        out.append(section)
    return out


def adtl_sections(structure: WavStructure) -> list[Section]:
    """Text labels and notes of cue points (LIST adtl: labl, note, ltxt)."""
    section = Section("wav_adtl", "Cue point labels")
    for chunk in structure.lists("adtl"):
        for child in chunk.children:
            d = child.data
            cue_id = int.from_bytes(d[0:4], "little") if len(d) >= 4 else None
            if child.fourcc in ("labl", "note"):
                kind = "Label" if child.fourcc == "labl" else "Note"
                section.add(f"{child.fourcc}_{cue_id}", f"{kind} of cue point {cue_id}",
                            _text(d[4:]), sensitive=True)
            elif child.fourcc == "ltxt":
                length = int.from_bytes(d[4:8], "little") if len(d) >= 8 else 0
                text = _text(d[20:]) if len(d) > 20 else ""
                section.add(f"ltxt_{cue_id}", f"Text of cue point {cue_id}",
                            text or f"{length} samples", sensitive=True,
                            note=f"region of {length} samples")
            else:
                section.add(f"{child.fourcc.strip()}_{child.offset}", f"Chunk {child.label}",
                            f"{child.size} bytes", sensitive=True, note="contents not examined")
    return [section] if section.fields else []


def _bext_text(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("latin-1").strip()


def bext_sections(structure: WavStructure, warnings: list[str]) -> list[Section]:
    """The Broadcast Wave Format extension (EBU Tech 3285)."""
    out = []
    for chunk in structure.all("bext"):
        d = chunk.data
        section = Section("wav_bext", "Broadcast WAV (bext)")
        if len(d) < BEXT_FIXED_SIZE:
            warnings.append(f"bext chunk at offset {chunk.offset} is too short "
                            f"({len(d)} bytes)")
            section.add("raw", "bext chunk", f"{len(d)} bytes", sensitive=True,
                        note="too short to read")
            out.append(section)
            continue
        version = int.from_bytes(d[346:348], "little")
        for key, label, raw in (("description", "Description", d[0:256]),
                                ("originator", "Originator", d[256:288]),
                                ("originator_reference", "Originator reference", d[288:320]),
                                ("origination_date", "Origination date", d[320:330]),
                                ("origination_time", "Origination time", d[330:338])):
            text = _bext_text(raw)
            if text:
                section.add(key, label, text, sensitive=True)
        section.add("time_reference", "Time reference",
                    f"sample {int.from_bytes(d[338:346], 'little')}")
        section.add("version", "Version", version)
        umid = d[348:412]
        if any(umid):
            section.add("umid", "UMID", umid.rstrip(b"\x00").hex(), sensitive=True,
                        note="unique material identifier")
        if version >= 2:
            for n, (key, label) in enumerate((
                    ("loudness_value", "Integrated loudness"), ("loudness_range", "Loudness range"),
                    ("max_true_peak_level", "Maximum true peak"),
                    ("max_momentary_loudness", "Maximum momentary loudness"),
                    ("max_short_term_loudness", "Maximum short-term loudness"))):
                value = int.from_bytes(d[412 + 2 * n:414 + 2 * n], "little", signed=True)
                section.add(key, label, f"{value / 100:.2f}")
        history = _text(d[BEXT_FIXED_SIZE:])
        if history:
            section.add("coding_history", "Coding history", history, sensitive=True)
        out.append(section)
    return out


def _xml_fields(raw: bytes, key: str, title: str, warnings: list[str]) -> list[Section]:
    """The elements of an XML chunk (iXML, axml) with text, as paths."""
    from metaex.core.xmldoc import parse_xml

    section = Section(key, title)
    text = raw.rstrip(b"\x00 \r\n\t")
    try:
        doc = parse_xml(text, f"{title} chunk")
    except MalformedFileError as exc:
        warnings.append(f"{title} chunk could not be read: {exc}")
        section.add("raw", f"{title} chunk", f"{len(raw)} bytes", sensitive=True,
                    note=f"not examined: {exc}")
        return [section]

    def walk(element, path: str) -> None:
        for child in element.elements():
            if len(section.fields) >= MAX_XML_FIELDS:
                return
            child_path = f"{path}/{child.local}"
            own = child.own_text().strip()
            if own:
                section.add(child_path, child_path, own[:MAX_SHOWN_CHARS], sensitive=True)
            walk(child, child_path)

    root = doc.root
    own = root.own_text().strip()
    if own:
        section.add(root.local, root.local, own[:MAX_SHOWN_CHARS], sensitive=True)
    walk(root, root.local)
    if len(section.fields) >= MAX_XML_FIELDS:
        warnings.append(f"only the first {MAX_XML_FIELDS} values of the {title} chunk are shown")
    if not section.fields:
        section.add("raw", f"{title} chunk", f"{len(raw)} bytes", sensitive=True,
                    note="no values")
    return [section]


def xml_sections(structure: WavStructure, warnings: list[str]) -> list[Section]:
    out = []
    for chunk in structure.all("iXML"):
        out += _xml_fields(chunk.data, "wav_ixml", "iXML", warnings)
    for chunk in structure.all("axml"):
        out += _xml_fields(chunk.data, "wav_axml", "axml", warnings)
    return out


def xmp_sections(structure: WavStructure, warnings: list[str]) -> list[Section]:
    section = Section("xmp", "XMP")
    for i, chunk in enumerate(structure.all("_PMX")):
        label = "XMP packet" if i == 0 else f"XMP packet {i + 1}"
        xmp.add_to_section(section, chunk.data.rstrip(b"\x00"), label, "", warnings)
    return [section] if section.fields else []


def id3_sections(structure: WavStructure, warnings: list[str],
                 pictures: list[Picture]) -> list[Section]:
    """ID3v2 tags stored in "id3 " (or "ID3 ") chunks."""
    out = []
    chunks = structure.all("id3 ", "ID3 ")
    for n, chunk in enumerate(chunks):
        title = f"ID3v2 tag in the {chunk.fourcc.strip()} chunk at offset {chunk.offset}"
        try:
            tag = id3.parse_tag(chunk.data, 0)
        except MalformedFileError as exc:
            warnings.append(f"the {chunk.fourcc.strip()} chunk at offset {chunk.offset} is not "
                            f"a valid ID3v2 tag: {exc}")
            section = Section("id3v2", title)
            section.add("raw", "ID3 chunk", f"{chunk.size} bytes", sensitive=True,
                        note=f"not examined: {exc}")
            out.append(section)
            continue
        out.append(id3.tag_section(tag, "id3v2", title.replace("ID3v2", tag.version, 1),
                                   warnings, pictures))
        hidden = id3.padding_section([tag], warnings)
        if hidden is not None:
            out.append(hidden)
        extra = chunk.data[tag.total_size:]
        if extra.strip(b"\x00"):
            warnings.append(f"CRITICAL: {len(extra)} bytes after the ID3v2 tag in the "
                            f"{chunk.fourcc.strip()} chunk at offset {chunk.offset}")
            section = Section("hidden_chunk_data", "Hidden data in chunks")
            section.add(f"id3_{chunk.offset}", f"After the ID3v2 tag at offset {chunk.offset}",
                        f"{len(extra)} bytes", sensitive=True)
            out.append(section)
    return out


@dataclass
class CuePoint:
    id: int
    position: int
    chunk_id: str
    chunk_start: int
    block_start: int
    sample_offset: int


def parse_cue(chunk: Chunk) -> list[CuePoint]:
    d = chunk.data
    if len(d) < 4:
        raise MalformedFileError("cue chunk is too short")
    count = int.from_bytes(d[0:4], "little")
    if count > MAX_CUE_POINTS or 4 + 24 * count > len(d):
        raise MalformedFileError(f"cue chunk says it has {count} cue points, but it holds "
                                 f"{(len(d) - 4) // 24}")
    points = []
    for i in range(count):
        p = d[4 + 24 * i:28 + 24 * i]
        points.append(CuePoint(int.from_bytes(p[0:4], "little"),
                               int.from_bytes(p[4:8], "little"), _fourcc(p[8:12]),
                               int.from_bytes(p[12:16], "little"),
                               int.from_bytes(p[16:20], "little"),
                               int.from_bytes(p[20:24], "little")))
    return points


def cue_bytes(points: list[CuePoint]) -> bytes:
    """A cue chunk's payload holding exactly these points."""
    out = [len(points).to_bytes(4, "little")]
    for p in points:
        out.append(p.id.to_bytes(4, "little") + p.position.to_bytes(4, "little")
                   + p.chunk_id.encode("latin-1") + p.chunk_start.to_bytes(4, "little")
                   + p.block_start.to_bytes(4, "little") + p.sample_offset.to_bytes(4, "little"))
    return b"".join(out)


@dataclass
class Sampler:
    manufacturer: int
    product: int
    sample_period: int
    unity_note: int
    pitch_fraction: int
    smpte_format: int
    smpte_offset: int
    loops: list[bytes]
    """Each loop's 24 bytes: ID, type, start, end, fraction, play count."""
    sampler_data: bytes


def parse_smpl(chunk: Chunk) -> Sampler:
    d = chunk.data
    if len(d) < 36:
        raise MalformedFileError("smpl chunk is too short")
    v = [int.from_bytes(d[i:i + 4], "little") for i in range(0, 36, 4)]
    loops, data_size = v[7], v[8]
    if loops > MAX_CUE_POINTS or 36 + 24 * loops + data_size > len(d):
        raise MalformedFileError(f"smpl chunk says it has {loops} loops and {data_size} bytes "
                                 f"of sampler data, more than its {len(d)} bytes")
    return Sampler(v[0], v[1], v[2], v[3], v[4], v[5], v[6],
                   [d[36 + 24 * i:60 + 24 * i] for i in range(loops)],
                   d[36 + 24 * loops:36 + 24 * loops + data_size])


def smpl_bytes(s: Sampler) -> bytes:
    """A smpl chunk's payload with the loops, without the manufacturer,
    product and sampler-specific data (they identify the sampler)."""
    values = (0, 0, s.sample_period, s.unity_note, s.pitch_fraction, s.smpte_format,
              s.smpte_offset, len(s.loops), 0)
    return b"".join(v.to_bytes(4, "little") for v in values) + b"".join(s.loops)


def cue_sections(structure: WavStructure, warnings: list[str]) -> list[Section]:
    out = []
    for chunk in structure.all("cue "):
        section = Section("wav_cue", "Cue points")
        try:
            points = parse_cue(chunk)
        except MalformedFileError as exc:
            warnings.append(f"malformed WAV: {exc}")
            continue
        for p in points[:1000]:
            section.add(f"cue_{p.id}", f"Cue point {p.id}", f"sample {p.sample_offset}")
        if len(points) > 1000:
            section.add("more", "More", f"{len(points) - 1000} more cue points")
        if not points:
            section.add("count", "Cue points", 0)
        extra = len(chunk.data) - 4 - 24 * len(points)
        if extra:
            section.add("extra", "Extra bytes", f"{extra} bytes after the cue points",
                        sensitive=True)
        out.append(section)
    for chunk in structure.all("smpl"):
        section = Section("wav_smpl", "Sampler loops (smpl)")
        try:
            s = parse_smpl(chunk)
        except MalformedFileError as exc:
            warnings.append(f"malformed WAV: {exc}")
            continue
        if s.manufacturer:
            section.add("manufacturer", "Manufacturer (MIDI ID)", s.manufacturer)
        if s.product:
            section.add("product", "Product", s.product)
        section.add("loops", "Loops", len(s.loops))
        if s.unity_note != 60:
            section.add("midi_unity_note", "MIDI unity note", s.unity_note)
        if s.sampler_data:
            section.add("sampler_data", "Sampler-specific data", f"{len(s.sampler_data)} bytes",
                        sensitive=True)
        extra = len(chunk.data) - 36 - 24 * len(s.loops) - len(s.sampler_data)
        if extra:
            section.add("extra", "Extra bytes", f"{extra} bytes after the loops",
                        sensitive=True)
        out.append(section)
    return out


def padding_sections(structure: WavStructure, warnings: list[str]) -> list[Section]:
    """JUNK, PAD and FLLR chunks (and padding bytes after odd-sized chunks):
    zeros are padding, anything else is hidden data."""
    hidden = Section("hidden_chunk_data", "Hidden data in padding chunks")
    padding = Section("audio_padding", "Padding chunks")
    hidden_total = 0
    for chunk in structure.chunks:
        if chunk.fourcc in PADDING_CHUNKS:
            nonzero = len(chunk.data) - chunk.data.count(0)
            if nonzero:
                hidden_total += nonzero
                hidden.add(f"{chunk.fourcc.strip()}_{chunk.offset}",
                           f"{chunk.label} chunk at offset {chunk.offset}",
                           f"{nonzero} non-zero bytes in {chunk.size} bytes", sensitive=True)
            else:
                padding.add(f"{chunk.fourcc.strip()}_{chunk.offset}",
                            f"{chunk.label} chunk at offset {chunk.offset}",
                            f"{chunk.size} zero bytes")
        for c in [chunk] + chunk.children:
            if c.padding not in (b"", b"\x00"):
                hidden_total += 1
                hidden.add(f"pad_{c.offset}", f"Padding byte of the {c.label} chunk",
                           f"0x{c.padding[0]:02x} (should be zero)", sensitive=True,
                           note=f"chunk at offset {c.offset}")
    out = []
    if hidden.fields:
        warnings.append(f"CRITICAL: {hidden_total} bytes of data hidden in padding chunks")
        out.append(hidden)
    if padding.fields:
        out.append(padding)
    return out


def unknown_sections(structure: WavStructure) -> list[Section]:
    """Chunks metaex doesn't read (they are all removed by strip)."""
    section = Section("unknown", "Other chunks")
    for chunk in structure.chunks:
        if chunk.fourcc == "LIST" and chunk.list_type in LIST_TYPES:
            continue
        if chunk.fourcc in KNOWN_CHUNKS and chunk.fourcc != "LIST":
            continue
        note = OTHER_CHUNK_NOTES.get(chunk.fourcc, "contents not examined")
        section.add(f"{chunk.label}@{chunk.offset}", chunk.label,
                    f"{chunk.size} bytes at offset {chunk.offset}", sensitive=True, note=note)
    return [section] if section.fields else []


def trailing_sections(structure: WavStructure, data: bytes,
                      warnings: list[str]) -> list[Section]:
    """Bytes after the end of the RIFF data, which players ignore."""
    if structure.end is None or len(data) <= structure.end:
        return []
    trailing = data[structure.end:]
    if trailing.count(0) == len(trailing):
        section = Section("audio_padding", "Trailing data")
        section.add("trailing", "After the end of the RIFF data", f"{len(trailing)} zero bytes",
                    note=f"from offset {structure.end}")
        warnings.append(f"{len(trailing)} bytes of padding after the end of the RIFF data")
        return [section]
    section = Section("audio_trailing", "Data after the end of the audio")
    section.add("size", "Size", f"{len(trailing)} bytes", sensitive=True)
    section.add("offset", "Starts at offset", structure.end)
    content = _trailing_content(trailing)
    section.add("content", "Content", content, sensitive=True,
                note="detected from the first bytes; more may follow")
    warnings.append(f"CRITICAL: {len(trailing)} bytes after the end of the RIFF data "
                    f"({content}); players ignore them, so they can hide anything")
    return [section]


def _trailing_content(trailing: bytes) -> str:
    """What the trailing data looks like: RIFF chunks a tagger appended
    without updating the RIFF size, a known file type, or unknown."""
    from metaex.core import filetype

    probe = WavStructure(0)
    names: list[Chunk] = []
    try:
        _Walker(trailing, probe).chunks(0, len(trailing), " (after the RIFF data)", names)
    except MalformedFileError:
        names = []
    if names and all(c.fourcc.isascii() and c.fourcc.isprintable() for c in names):
        return "chunks outside the RIFF data: " + ", ".join(c.label for c in names[:10])
    detected = filetype.detect(trailing)
    return detected.name if detected.key != filetype.UNKNOWN.key else "unrecognized data"


def structure_section(structure: WavStructure) -> Section:
    section = Section("structure", "WAV structure")
    section.add("riff_size", "RIFF size", structure.riff_size)
    n = 0
    for chunk in structure.chunks:
        section.add(f"chunk_{n}", chunk.label, f"offset {chunk.offset}, {chunk.size} bytes")
        n += 1
        for child in chunk.children:
            section.add(f"chunk_{n}", f"  {child.label}",
                        f"offset {child.offset}, {child.size} bytes")
            n += 1
    return section


# -- parse ------------------------------------------------------------------------------------


def parse(data: bytes | bytearray | memoryview) -> dict:
    """Parse a WAV file and return its warnings, report sections and structure.

    Raises MalformedFileError if the data has no RIFF WAVE header (and for
    RF64/BW64); problems later in the file become warnings.
    """
    data = bytes(data)
    structure = walk_chunks(data)
    warnings = list(structure.warnings)
    pictures: list[Picture] = []
    sections: list[Section] = []
    sections += format_section(structure, warnings)
    sections += info_sections(structure)
    sections += bext_sections(structure, warnings)
    sections += xml_sections(structure, warnings)
    sections += xmp_sections(structure, warnings)
    sections += id3_sections(structure, warnings, pictures)
    sections += adtl_sections(structure)
    sections += cue_sections(structure, warnings)
    sections += picture_sections(pictures, warnings, "audio_pictures",
                                 "Embedded pictures and objects")
    sections += padding_sections(structure, warnings)
    sections += unknown_sections(structure)
    sections += trailing_sections(structure, data, warnings)
    complete = structure.error is None and not any(w.startswith("malformed WAV")
                                                   for w in warnings)
    if structure.first("fmt ") is None or structure.first("data") is None:
        warnings.append("the file has no " + ("fmt" if structure.first("fmt ") is None
                                               else "data") + " chunk")
        complete = False
    return {"warnings": warnings, "sections": sections, "structure": structure_section(structure),
            "complete": complete}


# -- Stripping --------------------------------------------------------------------------------


def make_chunk(fourcc: str, payload: bytes) -> bytes:
    """A chunk with its size and, for an odd size, a zero padding byte."""
    return (fourcc.encode("latin-1") + len(payload).to_bytes(4, "little") + payload
            + (b"\x00" if len(payload) % 2 else b""))


def checked_structure(data: bytes) -> WavStructure:
    """Walk a WAV for stripping or verification: anything malformed raises.

    Refused: a truncated file or chunk, not exactly one fmt and one data
    chunk (fmt first), a broken fmt, fact, cue or smpl chunk, and bytes after
    the format description in the fmt chunk (the fmt chunk is copied byte
    for byte, so they couldn't be removed).
    """
    structure = walk_chunks(data)
    if structure.error:
        raise MalformedFileError(structure.error)
    fmts, datas = structure.all("fmt "), structure.all("data")
    if len(fmts) != 1 or len(datas) != 1:
        raise MalformedFileError(f"the file has {len(fmts)} fmt and {len(datas)} data chunks "
                                 "(exactly one of each is needed)")
    if structure.chunks.index(fmts[0]) > structure.chunks.index(datas[0]):
        raise MalformedFileError("the fmt chunk comes after the data chunk")
    fmt = parse_format(fmts[0])
    if fmt.extra and fmt.extra.count(0) != len(fmt.extra):
        raise HiddenDataError(f"the fmt chunk holds {len(fmt.extra)} bytes after the format "
                              "description; the fmt chunk is copied byte for byte, so they "
                              "can't be removed")
    for chunk in structure.all("fact"):
        if len(chunk.data) < 4:
            raise MalformedFileError("fact chunk is too short")
    for chunk in structure.all("cue "):
        parse_cue(chunk)
    for chunk in structure.all("smpl"):
        parse_smpl(chunk)
    return structure


def kept_chunks(structure: WavStructure, paranoid: bool = False) -> list[bytes]:
    """The chunks a stripped WAV holds, in the original order: fmt and data
    as they are, the first fact (its sample count), and (unless
    ``paranoid``) the first cue and smpl chunks rebuilt from their fields."""
    out: list[bytes] = []
    seen: set[str] = set()
    for chunk in structure.chunks:
        fourcc = chunk.fourcc
        if fourcc in seen:
            continue
        if fourcc in ("fmt ", "data"):
            out.append(make_chunk(fourcc, chunk.data))
        elif fourcc == "fact":
            out.append(make_chunk(fourcc, chunk.data[:4]))
        elif fourcc == "cue " and not paranoid:
            points = parse_cue(chunk)
            if not points:
                continue
            out.append(make_chunk(fourcc, cue_bytes(points)))
        elif fourcc == "smpl" and not paranoid:
            out.append(make_chunk(fourcc, smpl_bytes(parse_smpl(chunk))))
        else:
            continue
        seen.add(fourcc)
    return out


def riff(chunks: list[bytes]) -> bytes:
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def strip(data: bytes | bytearray | memoryview, *, keep_orientation: bool = True,
          paranoid: bool = False) -> bytes:
    """Return a copy of the WAV with all metadata removed (whitelist).

    Kept, in their original order: fmt and data, byte for byte; fact (the
    sample count of compressed formats). Kept by default: cue points (cue)
    and sampler loops (smpl), rebuilt from their positions only, without
    the sampler's manufacturer, product and vendor data; ``paranoid``
    removes them too. Removed: LIST INFO, LIST adtl (cue labels), bext,
    iXML, axml, _PMX (XMP), id3 chunks, JUNK/PAD/FLLR, every other chunk
    and anything after the RIFF data. Padding bytes are written as zero and
    the RIFF size is recomputed. ``keep_orientation`` doesn't apply to audio.

    Raises MalformedFileError for malformed input (HiddenDataError for data
    hidden inside the fmt chunk); nothing is ever partly stripped.
    """
    data = bytes(data)
    structure = checked_structure(data)
    cleaned = riff(kept_chunks(structure, paranoid))
    # Self-check: the output is a well-formed WAV with the same fmt and data.
    out = checked_structure(cleaned)
    if (out.first("fmt ").data != structure.first("fmt ").data
            or out.first("data").data != structure.first("data").data
            or len(cleaned) != out.end):
        raise MalformedFileError("internal check failed: the audio changed while stripping")
    return cleaned


def kept_warnings(cleaned: bytes) -> list[str]:
    """What a stripped WAV still holds by design: cue points and loops."""
    try:
        structure = walk_chunks(bytes(cleaned))
    except MalformedFileError:
        return []
    kept = [name for fourcc, name in (("cue ", "cue points"), ("smpl", "sampler loops"))
            if structure.first(fourcc) is not None]
    if not kept:
        return []
    return [f"{' and '.join(kept)} are kept (positions only, without labels); use "
            "--paranoid to remove them"]


def strip_warnings(data: bytes, paranoid: bool = False) -> list[str]:
    """Warnings about what stripping with these options takes away."""
    if not paranoid:
        return []
    try:
        structure = walk_chunks(bytes(data))
    except MalformedFileError:
        return []
    removed = [name for fourcc, name in (("cue ", "cue points (markers)"),
                                         ("smpl", "sampler loops"))
               if structure.first(fourcc) is not None]
    if not removed:
        return []
    return [f"--paranoid removed the {' and the '.join(removed)}"]


def removed_sizes(data: bytes | bytearray | memoryview) -> dict[str, int]:
    """Bytes after the RIFF data, for the strip summary."""
    data = bytes(data)
    try:
        structure = walk_chunks(data)
    except MalformedFileError:
        return {}
    if structure.end is None or len(data) <= structure.end:
        return {}
    trailing = data[structure.end:]
    key = "audio_padding" if trailing.count(0) == len(trailing) else "audio_trailing"
    return {key: len(trailing)}


# -- Verification -------------------------------------------------------------------------------

IMAGE_LABEL = "Audio (fmt and data chunks)"


def is_kept_by_design(section_key: str, field) -> bool:
    """True if a field may stay in a stripped WAV: the format, the cue
    points and the number of loops (not extra bytes or the sampler's
    identity)."""
    if section_key == "wav_format":
        return True
    if section_key == "wav_cue":
        return field.key.startswith("cue_") or field.key in ("more", "count")
    if section_key == "wav_smpl":
        return field.key in ("loops", "midi_unity_note")
    return False


def _record(fourcc: str, payload: bytes) -> bytes:
    return fourcc.encode("latin-1") + len(payload).to_bytes(8, "big") + payload


def audio_data(structure: WavStructure) -> bytes:
    """The fmt and data payloads, for comparing two files."""
    return (_record("fmt ", structure.first("fmt ").data)
            + _record("data", structure.first("data").data))


def _wave_frames(data: bytes) -> tuple[tuple, bytes]:
    """(parameters, frames) as the standard library's wave module reads them;
    raises wave.Error, EOFError or ValueError if it can't."""
    with wave.open(io.BytesIO(data), "rb") as reader:
        params = (reader.getnchannels(), reader.getsampwidth(), reader.getframerate(),
                  reader.getnframes())
        return params, reader.readframes(reader.getnframes())


def _chunk_problem(chunk: Chunk, seen: set[str]) -> str | None:
    """Why a chunk is not one strip() would write; None if it is."""
    first = chunk.fourcc not in seen
    seen.add(chunk.fourcc)
    if chunk.padding not in (b"", b"\x00"):
        return "padding byte is not zero"
    if not first:
        return f"a second {chunk.label} chunk"
    if chunk.fourcc in ("fmt ", "data"):
        return None
    if chunk.fourcc == "fact":
        return None if chunk.size == 4 else "fact chunk holds more than the sample count"
    if chunk.fourcc == "cue ":
        return None if cue_bytes(parse_cue(chunk)) == chunk.data else (
            "cue chunk holds more than its cue points")
    if chunk.fourcc == "smpl":
        return None if smpl_bytes(parse_smpl(chunk)) == chunk.data else (
            "smpl chunk holds more than its loops")
    return f"{chunk.label} chunk is not allowed in a cleaned WAV"


def verify_details(cleaned: bytes, original: bytes | None = None) -> dict:
    """WAV-specific verification, beyond the field-level whitelist.

    - The file must be complete and well-formed, with the RIFF size equal to
      the file size minus 8 (nothing after the RIFF data).
    - Chunk whitelist: one fmt, one data, at most one fact (sample count
      only), one cue (cue points only) and one smpl (loops only, without the
      sampler's identity); padding bytes must be zero.
    - SHA-256 of the fmt and data payloads, compared with the original's if
      one is given. For PCM audio the frames are also read with Python's
      wave module from both files and compared.
    """
    out: dict = {"leftovers": [], "problems": [], "notes": [], "image_sha256": None,
                 "original_image_sha256": None, "image_data_match": None,
                 "image_label": IMAGE_LABEL}
    try:
        structure = checked_structure(bytes(cleaned))
    except MalformedFileError as exc:
        out["problems"].append(f"not a complete, well-formed WAV: {exc}")
        return out
    out["image_sha256"] = hashlib.sha256(audio_data(structure)).hexdigest()
    if structure.end != len(cleaned):
        out["leftovers"].append(("WAV structure", f"data at offset {structure.end}",
                                 f"{len(cleaned) - structure.end} bytes after the RIFF data"))
    seen: set[str] = set()
    for chunk in structure.chunks:
        problem = _chunk_problem(chunk, seen)
        if problem is not None:
            out["leftovers"].append(("WAV structure", f"{chunk.label} at offset {chunk.offset}",
                                     f"{problem} ({chunk.size} bytes)"))
    if original is None:
        return out
    try:
        orig_structure = checked_structure(bytes(original))
    except MalformedFileError as exc:
        out["problems"].append(f"original is not a complete, well-formed WAV ({exc}); audio "
                               "not compared")
        return out
    out["original_image_sha256"] = hashlib.sha256(audio_data(orig_structure)).hexdigest()
    match = out["original_image_sha256"] == out["image_sha256"]
    if not match:
        out["problems"].append("the fmt or data chunk differs from the original")
    if parse_format(structure.first("fmt ")).code == WAVE_FORMAT_PCM:
        match = _compare_frames(bytes(cleaned), bytes(original), out) and match
    out["image_data_match"] = match
    return out


def _compare_frames(cleaned: bytes, original: bytes, out: dict) -> bool:
    """Compare the PCM frames as the wave module reads them; False if they differ."""
    try:
        orig_params, orig_frames = _wave_frames(original)
    except (wave.Error, EOFError, ValueError) as exc:
        out["notes"].append(f"Python's wave module can't read the original ({exc}); the "
                            "frames were compared byte for byte only")
        return True
    try:
        params, frames = _wave_frames(cleaned)
    except (wave.Error, EOFError, ValueError) as exc:
        out["problems"].append(f"Python's wave module can't read the cleaned file ({exc})")
        return False
    if (params, frames) != (orig_params, orig_frames):
        out["problems"].append("the PCM frames read with Python's wave module differ from the "
                               "original's")
        return False
    return True
