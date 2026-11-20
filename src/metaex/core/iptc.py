"""Photoshop image resources (8BIM blocks) and IPTC-IIM records.

Used by JPEG (APP13 "Photoshop 3.0") and later TIFF (tags 0x8649, 0x83BB).
"""

from __future__ import annotations

from dataclasses import dataclass

from metaex.core.errors import MalformedFileError
from metaex.core.reader import ByteReader
from metaex.core.result import Section

MAX_RESOURCES = 1000
MAX_DATASETS = 10_000

RESOURCE_SIGNATURES = {b"8BIM", b"PHUT", b"AgHg", b"DCSR", b"MeSa"}
IPTC_NAA = 0x0404
PHOTOSHOP_THUMBNAILS = {0x0409, 0x040C}

RESOURCE_NAMES = {
    0x03E8: "Channels, rows, columns (obsolete)",
    0x03ED: "Resolution info",
    0x03EE: "Alpha channel names",
    0x03F0: "Caption (obsolete)",
    0x03F2: "Background color",
    0x03F3: "Print flags",
    0x03F5: "Color halftoning info",
    0x03F8: "Color transfer functions",
    0x0400: "Layer state info",
    0x0402: "Layers group info",
    0x0404: "IPTC-NAA record",
    0x0406: "JPEG quality",
    0x0408: "Grid and guides info",
    0x0409: "Photoshop thumbnail (old format)",
    0x040A: "Copyright flag",
    0x040B: "URL",
    0x040C: "Photoshop thumbnail",
    0x040D: "Global angle",
    0x040F: "ICC profile",
    0x0410: "Watermark",
    0x0411: "ICC untagged profile",
    0x0414: "Document-specific IDs seed",
    0x0419: "Global altitude",
    0x041A: "Slices",
    0x041E: "URL list",
    0x0421: "Version info",
    0x0422: "EXIF data 1",
    0x0423: "EXIF data 3",
    0x0424: "XMP metadata",
    0x0425: "IPTC digest",
    0x0426: "Print scale",
    0x0428: "Pixel aspect ratio",
    0x043A: "Print information",
    0x043B: "Print style",
    0x0BB7: "Clipping path name",
    0x2710: "Print flags information",
}
SENSITIVE_RESOURCES = {0x03F0, 0x0404, 0x0409, 0x040B, 0x040C, 0x041E, 0x0422, 0x0423, 0x0424}

IPTC1_NAMES = {
    0: "EnvelopeRecordVersion", 5: "Destination", 20: "FileFormat", 22: "FileVersion",
    30: "ServiceIdentifier", 40: "EnvelopeNumber", 50: "ProductID", 60: "EnvelopePriority",
    70: "DateSent", 80: "TimeSent", 90: "CodedCharacterSet", 100: "UniqueObjectName",
    120: "ARMIdentifier", 122: "ARMVersion",
}
IPTC2_NAMES = {
    0: "RecordVersion", 3: "ObjectTypeReference", 4: "ObjectAttributeReference",
    5: "ObjectName", 7: "EditStatus", 10: "Urgency", 12: "SubjectReference", 15: "Category",
    20: "SupplementalCategories", 22: "FixtureIdentifier", 25: "Keywords",
    26: "ContentLocationCode", 27: "ContentLocationName", 30: "ReleaseDate",
    35: "ReleaseTime", 37: "ExpirationDate", 38: "ExpirationTime",
    40: "SpecialInstructions", 42: "ActionAdvised", 45: "ReferenceService",
    47: "ReferenceDate", 50: "ReferenceNumber", 55: "DateCreated", 60: "TimeCreated",
    62: "DigitalCreationDate", 63: "DigitalCreationTime", 65: "OriginatingProgram",
    70: "ProgramVersion", 75: "ObjectCycle", 80: "By-line", 85: "By-lineTitle",
    90: "City", 92: "Sub-location", 95: "Province-State",
    100: "Country-PrimaryLocationCode", 101: "Country-PrimaryLocationName",
    103: "OriginalTransmissionReference", 105: "Headline", 110: "Credit", 115: "Source",
    116: "CopyrightNotice", 118: "Contact", 120: "Caption-Abstract", 121: "LocalCaption",
    122: "Writer-Editor", 125: "RasterizedCaption", 130: "ImageType",
    131: "ImageOrientation", 135: "LanguageIdentifier", 150: "AudioType",
    200: "ObjectPreviewFileFormat", 201: "ObjectPreviewFileVersion",
    202: "ObjectPreviewData",
}
_NOT_SENSITIVE = {(1, 0), (1, 90), (2, 0)}
_BINARY = {(1, 0), (1, 20), (1, 22), (2, 0), (2, 125), (2, 200), (2, 201), (2, 202)}
UTF8_MARKER = b"\x1b%G"


@dataclass
class ImageResource:
    id: int
    name: str
    data: bytes

    @property
    def description(self) -> str:
        if 0x07D0 <= self.id <= 0x0BB6:
            return "Path information"
        return RESOURCE_NAMES.get(self.id, "Unknown resource")


def parse_image_resources(data: bytes, warnings: list[str]) -> list[ImageResource]:
    """Walk 8BIM blocks. Problems end the walk and are added to warnings."""
    r = ByteReader(data, "big")
    resources: list[ImageResource] = []
    try:
        while r.remaining >= 12:
            if len(resources) >= MAX_RESOURCES:
                warnings.append(f"more than {MAX_RESOURCES} Photoshop resources; the rest "
                                "were not read")
                break
            signature = r.read(4, "resource signature")
            if signature not in RESOURCE_SIGNATURES:
                if signature != b"\x00\x00\x00\x00":
                    warnings.append(f"unexpected Photoshop resource signature {signature!r} "
                                    f"at offset {r.tell() - 4}; remaining data not read")
                break
            rid = r.u16("resource ID")
            name_length = r.u8("resource name length")
            name = r.read(name_length, "resource name").decode("latin-1")
            if (1 + name_length) % 2:
                r.skip(1, "resource name padding")
            size = r.u32("resource size")
            payload = r.read(size, f"resource 0x{rid:04X} data")
            if size % 2 and r.remaining:
                r.skip(1, "resource padding")
            resources.append(ImageResource(rid, name, payload))
    except MalformedFileError as exc:
        warnings.append(f"Photoshop resources are truncated: {exc}")
    return resources


def resources_section(resources: list[ImageResource], warnings: list[str]) -> Section:
    section = Section("photoshop", "Photoshop resources")
    for res in resources:
        label = f"0x{res.id:04X} {res.description}"
        value = f"{len(res.data)} bytes"
        if res.name:
            value += f', named "{res.name}"'
        section.add(f"0x{res.id:04x}", label, value, sensitive=res.id in SENSITIVE_RESOURCES)
        if res.id in PHOTOSHOP_THUMBNAILS:
            warnings.append("Photoshop thumbnail present: it may show an earlier, "
                            "uncropped or unedited version of the image")
    return section


def parse_iim(data: bytes, warnings: list[str]) -> list[tuple[int, int, bytes]]:
    """IPTC-IIM datasets as (record, dataset, value) in file order."""
    r = ByteReader(data, "big")
    out: list[tuple[int, int, bytes]] = []
    try:
        while r.remaining:
            if len(out) >= MAX_DATASETS:
                warnings.append(f"more than {MAX_DATASETS} IPTC datasets; the rest were not read")
                break
            marker = r.u8("IPTC tag marker")
            if marker != 0x1C:
                if marker != 0x00:  # zero padding at the end is common
                    warnings.append(f"unexpected byte 0x{marker:02X} in IPTC data at offset "
                                    f"{r.tell() - 1}; remaining data not read")
                break
            record = r.u8("IPTC record number")
            dataset = r.u8("IPTC dataset number")
            size = r.u16("IPTC dataset size")
            if size & 0x8000:  # extended dataset: size is in the next n bytes
                n = size & 0x7FFF
                if not 1 <= n <= 4:
                    raise MalformedFileError(f"invalid extended IPTC size length {n}")
                size = int.from_bytes(r.read(n, "IPTC extended size"), "big")
            out.append((record, dataset, r.read(size, f"IPTC {record}:{dataset} value")))
    except MalformedFileError as exc:
        warnings.append(f"IPTC data is truncated or malformed: {exc}")
    return out


def iptc_section(datasets: list[tuple[int, int, bytes]]) -> Section:
    """One field per dataset; repeated datasets (e.g. Keywords) are joined with "; "."""
    utf8 = any(rec == 1 and ds == 90 and value == UTF8_MARKER for rec, ds, value in datasets)
    grouped: dict[tuple[int, int], list[str]] = {}
    for record, dataset, value in datasets:
        grouped.setdefault((record, dataset), []).append(
            _format_dataset(record, dataset, value, utf8))
    section = Section("iptc", "IPTC")
    for (record, dataset), values in grouped.items():
        names = IPTC1_NAMES if record == 1 else IPTC2_NAMES if record == 2 else {}
        name = names.get(dataset)
        label = name or f"Unknown dataset {record}:{dataset}"
        section.add(f"{record}:{dataset}", label, "; ".join(values),
                    sensitive=(record, dataset) not in _NOT_SENSITIVE)
    return section


def _format_dataset(record: int, dataset: int, value: bytes, utf8: bool) -> str:
    if (record, dataset) == (1, 90):
        return "UTF-8" if value == UTF8_MARKER else value.hex(" ")
    if (record, dataset) in _BINARY:
        if len(value) == 2:
            return str(int.from_bytes(value, "big"))
        return f"<{len(value)} bytes>"
    if utf8:
        return value.decode("utf-8", errors="replace")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("latin-1")
