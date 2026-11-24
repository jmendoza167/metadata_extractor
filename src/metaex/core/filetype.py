"""Detect a file's real format from its content ("magic bytes").

Detection never trusts the file name: the name is only used to warn when
the extension doesn't match the content. Unrecognized data is reported as
the "unknown" type; detect() never raises on bad input.

``supported`` says whether the format is in metaex's scope. It does not
mean a parser for it already exists.
"""

from __future__ import annotations

import dataclasses
import io
import os
import re
import struct
import zipfile
from dataclasses import dataclass

from metaex.core.errors import MalformedFileError
from metaex.core.reader import ByteReader

# How far into a file we look for an SVG root element (and other headers).
SVG_SNIFF_LIMIT = 4096
# Upper bound on ftyp compatible brands we examine.
FTYP_MAX_BRANDS = 64
# ZIPs with more central directory entries than this are not inspected.
ZIP_MAX_ENTRIES = 100_000
# The ODF/EPUB "mimetype" entry is a short ASCII string.
ZIP_MIMETYPE_MAX_SIZE = 256


@dataclass(frozen=True)
class FileType:
    key: str
    """Short stable identifier, e.g. "jpeg"."""
    name: str
    """Display name, e.g. "JPEG"."""
    mime: str
    extensions: tuple[str, ...]
    """Usual extensions, lowercase with dot; the first is the canonical one."""
    supported: bool
    warnings: tuple[str, ...] = ()
    """Problems noticed during detection, for display to the user."""


def _t(key: str, name: str, mime: str, exts: str, supported: bool) -> FileType:
    return FileType(key, name, mime, tuple(exts.split()), supported)


# Images
JPEG = _t("jpeg", "JPEG", "image/jpeg", ".jpg .jpeg .jpe .jfif", True)
PNG = _t("png", "PNG", "image/png", ".png", True)
GIF = _t("gif", "GIF", "image/gif", ".gif", True)
WEBP = _t("webp", "WebP", "image/webp", ".webp", True)
SVG = _t("svg", "SVG", "image/svg+xml", ".svg", True)
TIFF = _t("tiff", "TIFF", "image/tiff", ".tif .tiff", False)
HEIC = _t("heic", "HEIC", "image/heic", ".heic .heif .hif", False)
HEIF = _t("heif", "HEIF", "image/heif", ".heif .heic .hif", False)
AVIF = _t("avif", "AVIF", "image/avif", ".avif", False)

# Documents
PDF = _t("pdf", "PDF", "application/pdf", ".pdf", False)
DOCX = _t(
    "docx", "DOCX",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".docx .docm .dotx .dotm", True,
)
XLSX = _t(
    "xlsx", "XLSX",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsx .xlsm .xltx .xltm", True,
)
PPTX = _t(
    "pptx", "PPTX",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pptx .pptm .potx .potm .ppsx .ppsm", True,
)
ODT = _t("odt", "ODT", "application/vnd.oasis.opendocument.text", ".odt .ott", True)
ODS = _t("ods", "ODS", "application/vnd.oasis.opendocument.spreadsheet", ".ods .ots", True)
ODP = _t("odp", "ODP", "application/vnd.oasis.opendocument.presentation", ".odp .otp", True)
EPUB = _t("epub", "EPUB", "application/epub+zip", ".epub", False)
OLE2 = _t(
    "ole2", "Legacy Office (OLE2)", "application/x-ole-storage",
    ".doc .xls .ppt .dot .xlt .pot .msg .msi", False,
)
# OLE2 files told apart by the streams in their directory (core/ole2.py).
DOC = _t("doc", "Word 97-2003 document (.doc)", "application/msword", ".doc .dot", False)
XLS = _t("xls", "Excel 97-2003 workbook (.xls)", "application/vnd.ms-excel", ".xls .xlt", False)
PPT = _t("ppt", "PowerPoint 97-2003 presentation (.ppt)", "application/vnd.ms-powerpoint",
         ".ppt .pot .pps", False)
ENCRYPTED_OFFICE = _t(
    "encrypted_office", "Password-protected Office document", "application/x-ole-storage",
    ".docx .docm .dotx .dotm .xlsx .xlsm .xltx .xltm .pptx .pptm .potx .potm .ppsx .ppsm", False,
)

# Audio / video
MP3 = _t("mp3", "MP3", "audio/mpeg", ".mp3", True)
WAV = _t("wav", "WAV", "audio/wav", ".wav .wave", True)
FLAC = _t("flac", "FLAC", "audio/flac", ".flac", True)
OGG = _t("ogg", "Ogg", "audio/ogg", ".ogg .oga .ogv .opus .spx .ogx", False)
MP4 = _t("mp4", "MP4", "video/mp4", ".mp4 .m4v", False)
MOV = _t("mov", "QuickTime", "video/quicktime", ".mov .qt", False)
M4A = _t("m4a", "M4A", "audio/mp4", ".m4a .m4b .m4p", False)

# Archives
ZIP = _t("zip", "ZIP", "application/zip", ".zip", True)
TAR = _t("tar", "TAR", "application/x-tar", ".tar", True)
GZIP = _t("gzip", "gzip", "application/gzip", ".gz .tgz", False)
SEVEN_ZIP = _t("7z", "7-Zip", "application/x-7z-compressed", ".7z", False)
RAR = _t("rar", "RAR", "application/vnd.rar", ".rar", False)

UNKNOWN = _t("unknown", "unknown", "application/octet-stream", "", False)

# Fixed signatures at offset 0, checked in order.
_MAGIC_AT_START: tuple[tuple[bytes, FileType], ...] = (
    (b"\xff\xd8\xff", JPEG),
    (b"\x89PNG\r\n\x1a\n", PNG),
    (b"GIF87a", GIF),
    (b"GIF89a", GIF),
    (b"II*\x00", TIFF),
    (b"MM\x00*", TIFF),
    (b"%PDF-", PDF),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", OLE2),
    (b"fLaC", FLAC),
    (b"OggS", OGG),
    (b"\x1f\x8b\x08", GZIP),
    (b"7z\xbc\xaf\x27\x1c", SEVEN_ZIP),
    (b"Rar!\x1a\x07\x00", RAR),
    (b"Rar!\x1a\x07\x01\x00", RAR),
)


def detect(data: bytes | bytearray | memoryview, filename: str | os.PathLike[str] | None = None) -> FileType:
    """Identify the format of ``data``. Never raises for any input bytes.

    The result is based on content only. If ``filename`` is given and its
    extension doesn't fit the detected type, a warning is added.
    """
    filetype = _detect_content(data)
    if filename is not None:
        warning = extension_mismatch(filetype, filename)
        if warning is not None:
            filetype = _with_warning(filetype, warning)
    return filetype


def extension_mismatch(filetype: FileType, filename: str | os.PathLike[str]) -> str | None:
    """Return a warning if the file's extension doesn't match its content.

    Comparison is case-insensitive, and all of a type's usual extensions
    count as equivalent (.jpg/.jpeg/.jpe, .tif/.tiff, ...). Files without an
    extension and files of unknown type produce no warning.
    """
    if filetype.key == UNKNOWN.key:
        return None
    ext = os.path.splitext(os.path.basename(os.fspath(filename)))[1]
    if not ext or ext.lower() in filetype.extensions:
        return None
    return f"extension {ext} but content is {filetype.name}"


def _detect_content(data: bytes | bytearray | memoryview) -> FileType:
    head = bytes(data[:SVG_SNIFF_LIMIT])

    # TAR first: its header checksum makes it the least ambiguous check,
    # and a TAR's first bytes are a member name that could be anything.
    if _is_tar(head):
        return TAR
    for magic, filetype in _MAGIC_AT_START:
        if head.startswith(magic):
            return _detect_ole2(data) if filetype is OLE2 else filetype
    if head.startswith(b"RIFF") and len(head) >= 12:
        if head[8:12] == b"WEBP":
            return WEBP
        if head[8:12] == b"WAVE":
            return WAV
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return _detect_zip(data)
    if head.startswith(b"ID3"):
        found = _detect_id3(head)
        if found is not None:
            return found
    if head[4:8] == b"ftyp":
        found = _detect_ftyp(head)
        if found is not None:
            return found
    if _is_mpeg_audio(head):
        return MP3
    if _looks_like_svg(head):
        return SVG
    return UNKNOWN


# -- OLE2 (legacy Office, encrypted Office) ----------------------------------------

def _detect_ole2(data: bytes | bytearray | memoryview) -> FileType:
    from metaex.core import ole2

    kind = ole2.classify(data)
    return {ole2.ENCRYPTED_OOXML: ENCRYPTED_OFFICE, ole2.WORD: DOC, ole2.EXCEL: XLS,
            ole2.POWERPOINT: PPT}.get(kind, OLE2)


_REFUSALS = {
    ENCRYPTED_OFFICE.key: ("this Office document is password-protected (encrypted), so metaex "
                           "can't read it. Remove the password in Word, Excel or PowerPoint "
                           "(File > Info > Protect > Encrypt with Password, and clear it) or "
                           "LibreOffice, save, and try again"),
    DOC.key: ("legacy Word 97-2003 documents (.doc) are not supported. Open it in Word or "
              "LibreOffice, save it as .docx, and try again"),
    XLS.key: ("legacy Excel 97-2003 workbooks (.xls) are not supported. Open it in Excel or "
              "LibreOffice, save it as .xlsx, and try again"),
    PPT.key: ("legacy PowerPoint 97-2003 presentations (.ppt) are not supported. Open it in "
              "PowerPoint or LibreOffice, save it as .pptx, and try again"),
    OLE2.key: ("legacy Office (OLE2 compound) files are not supported. Save Office documents "
               "in the current format (.docx, .xlsx, .pptx) and try again"),
}


def refusal(filetype: FileType) -> str | None:
    """Why metaex will never handle this type, with what to do instead; None
    for types without a specific reason."""
    return _REFUSALS.get(filetype.key)


# -- ISO base media (ftyp) -------------------------------------------------------

_AVIF_BRANDS = {b"avif", b"avis"}
_HEIC_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs"}
_HEIF_BRANDS = {b"mif1", b"msf1"}
_M4A_BRANDS = {b"M4A ", b"M4B ", b"M4P "}
_MOV_BRANDS = {b"qt  "}
_MP4_BRANDS = {
    b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42",
    b"avc1", b"dash", b"M4V ", b"M4VH", b"M4VP", b"f4v ", b"mmp4",
}
# Checked against the major brand first, then the compatible brands.
# HEIF (mif1/msf1) is a generic container brand and only used as a fallback.
_FTYP_TABLE = (
    (_AVIF_BRANDS, AVIF),
    (_HEIC_BRANDS, HEIC),
    (_M4A_BRANDS, M4A),
    (_MOV_BRANDS, MOV),
    (_MP4_BRANDS, MP4),
)


def _ftyp_brands(head: bytes) -> list[bytes]:
    """Major brand followed by compatible brands, bounded by box and data size."""
    if len(head) < 12:
        return []
    box_size = int.from_bytes(head[0:4], "big")
    if box_size == 0:  # box extends to end of file
        box_size = len(head)
    elif box_size < 12:  # too small to hold even the major brand
        return []
    end = min(box_size, len(head), 16 + 4 * FTYP_MAX_BRANDS)
    brands = [head[8:12]]
    for pos in range(16, end - 3, 4):
        brands.append(head[pos : pos + 4])
    return brands


def _detect_ftyp(head: bytes) -> FileType | None:
    brands = _ftyp_brands(head)
    if not brands:
        return None
    major, compatible = brands[0], set(brands[1:])
    for known, filetype in _FTYP_TABLE:
        if major in known:
            return filetype
    for known, filetype in _FTYP_TABLE:
        if compatible & known:
            return filetype
    if major in _HEIF_BRANDS or compatible & _HEIF_BRANDS:
        return HEIF
    return None


# -- ZIP and ZIP-based formats ---------------------------------------------------

_OOXML_FOLDERS = (("word/", DOCX), ("xl/", XLSX), ("ppt/", PPTX))
_ZIP_MIMETYPES = {
    "application/vnd.oasis.opendocument.text": ODT,
    "application/vnd.oasis.opendocument.text-template": ODT,
    "application/vnd.oasis.opendocument.spreadsheet": ODS,
    "application/vnd.oasis.opendocument.spreadsheet-template": ODS,
    "application/vnd.oasis.opendocument.presentation": ODP,
    "application/vnd.oasis.opendocument.presentation-template": ODP,
    "application/epub+zip": EPUB,
}
# zipfile can raise a variety of exceptions on hostile input.
_ZIP_ERRORS = (
    zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, ValueError,
    KeyError, IndexError, NotImplementedError, RuntimeError, struct.error,
)


def _detect_zip(data: bytes | bytearray | memoryview) -> FileType:
    """Tell plain ZIP apart from OOXML, ODF and EPUB without extracting.

    Only the central directory (file names) and the tiny "mimetype" entry
    are read. The entry count is checked from the end-of-central-directory
    record first, so a hostile archive can't make zipfile allocate millions
    of entries.
    """
    try:
        count = _zip_entry_count(data)
    except MalformedFileError:
        count = None
    if count is None:
        return _without_directory(data)
    if count > ZIP_MAX_ENTRIES:
        return _with_warning(
            ZIP, f"ZIP has {count} entries (limit {ZIP_MAX_ENTRIES}); contents not examined"
        )
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            mimetype = _zip_mimetype(zf)
    except _ZIP_ERRORS:
        return _without_directory(data)

    return _classify_zip_names(names, mimetype)


def _without_directory(data: bytes | bytearray | memoryview) -> FileType:
    """A ZIP whose directory can't be read (e.g. a truncated download): the
    entries at the start still say what the archive was meant to be."""
    found = _classify_zip_names(*_local_entries(data))
    warning = "ZIP directory could not be read (the file may be truncated)"
    return _with_warning(found, warning if found is not ZIP else f"{warning}; contents not examined")


def _classify_zip_names(names: set[str], mimetype: str | None) -> FileType:
    if mimetype in _ZIP_MIMETYPES:
        return _ZIP_MIMETYPES[mimetype]
    if "[Content_Types].xml" in names:
        found = {
            filetype
            for prefix, filetype in _OOXML_FOLDERS
            if any(name.startswith(prefix) for name in names)
        }
        if len(found) == 1:
            return found.pop()
    return ZIP


ZIP_LOCAL_SCAN_ENTRIES = 1000


def _local_entries(data: bytes | bytearray | memoryview) -> tuple[set[str], str | None]:
    """Names of the entries found by walking local headers from the start
    (at most ZIP_LOCAL_SCAN_ENTRIES), and the ODF mimetype if the first
    entry is a stored "mimetype". Stops at anything unexpected."""
    names: set[str] = set()
    mimetype = None
    r = ByteReader(data, "little")
    try:
        for _ in range(ZIP_LOCAL_SCAN_ENTRIES):
            if r.remaining < 30 or r.peek(4) != b"PK\x03\x04":
                break
            r.skip(6)
            flags, method = r.u16(), r.u16()
            r.skip(8)
            size = r.u32()
            r.skip(4)
            name_len, extra_len = r.u16(), r.u16()
            name = r.read(name_len).decode("utf-8", errors="replace")
            r.skip(extra_len)
            if name == "mimetype" and not names and method == 0 and size <= ZIP_MIMETYPE_MAX_SIZE:
                mimetype = r.peek(size).decode("ascii", errors="replace").strip()
            names.add(name)
            if flags & 0x0008:
                break  # sizes follow the data: the next header can't be found safely
            r.skip(size)
    except MalformedFileError:
        pass
    return names, mimetype


def _zip_entry_count(data: bytes | bytearray | memoryview) -> int | None:
    """Total entries from the EOCD (or ZIP64 EOCD) record, None if not found."""
    tail_start = max(0, len(data) - (22 + 0xFFFF))
    tail = bytes(data[tail_start:])
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        return None
    r = ByteReader(tail, "little")
    r.seek(eocd + 10)
    total = r.u16("ZIP entry count")
    if total != 0xFFFF:
        return total
    # ZIP64: a locator sits immediately before the EOCD record.
    locator = eocd - 20
    if locator < 0 or tail[locator : locator + 4] != b"PK\x06\x07":
        return None
    r.seek(locator + 8)
    z64_offset = r.u64("ZIP64 record offset")
    whole = ByteReader(data, "little")
    if z64_offset > len(whole) - 56:
        return None
    z64 = whole.sub(z64_offset, 56, "ZIP64 end of central directory")
    if z64.read(4) != b"PK\x06\x06":
        return None
    z64.seek(32)
    return z64.u64("ZIP64 entry count")


def _zip_mimetype(zf: zipfile.ZipFile) -> str | None:
    try:
        info = zf.getinfo("mimetype")
    except KeyError:
        return None
    if (
        info.file_size > ZIP_MIMETYPE_MAX_SIZE
        or info.compress_size > ZIP_MIMETYPE_MAX_SIZE
        or info.flag_bits & 0x1  # encrypted
    ):
        return None
    raw = zf.read(info)
    try:
        return raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return None


def _with_warning(filetype: FileType, warning: str) -> FileType:
    return dataclasses.replace(filetype, warnings=filetype.warnings + (warning,))


# -- Audio -----------------------------------------------------------------------

def _detect_id3(head: bytes) -> FileType | None:
    """An ID3v2 tag usually means MP3, but FLAC files sometimes carry one too."""
    if len(head) < 10 or head[3] not in (2, 3, 4) or head[4] == 0xFF:
        return None
    if any(b >= 0x80 for b in head[6:10]):  # sizes are "synchsafe" 7-bit bytes
        return None
    size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
    end = 10 + size + (10 if head[3] == 4 and head[5] & 0x10 else 0)
    if head[end : end + 4] == b"fLaC":
        return FLAC
    return MP3


_MPEG_KBPS = {
    # (MPEG-1?, layer bits) -> bitrates for indexes 0-14 (3 = Layer I, 2 = II, 1 = III)
    (True, 3): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (True, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (True, 1): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (False, 3): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (False, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (False, 1): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
# Version bits: 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5 (1 is reserved).
_MPEG_SAMPLE_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _mpeg_frame_length(data: bytes, pos: int) -> int | None:
    """Length of a valid MPEG audio frame (Layer I, II or III) starting at
    pos, else None. Free-format frames (bitrate index 0) don't count."""
    if pos < 0 or pos + 4 > len(data):
        return None
    b0, b1, b2 = data[pos], data[pos + 1], data[pos + 2]
    if b0 != 0xFF or b1 & 0xE0 != 0xE0 or data[pos + 3] & 3 == 2:
        return None
    version = (b1 >> 3) & 0x3
    layer = (b1 >> 1) & 0x3
    bitrate_index = b2 >> 4
    rate_index = (b2 >> 2) & 0x3
    if version == 1 or layer == 0 or bitrate_index in (0, 15) or rate_index == 3:
        return None
    bitrate = _MPEG_KBPS[(version == 3, layer)][bitrate_index] * 1000
    sample_rate = _MPEG_SAMPLE_RATES[version][rate_index]
    padding = (b2 >> 1) & 0x1
    if layer == 3:
        return (12 * bitrate // sample_rate + padding) * 4
    samples = 1152 if layer == 2 or version == 3 else 576
    return samples // 8 * bitrate // sample_rate + padding


def _is_free_format_mpeg(head: bytes) -> bool:
    """A free-format frame header (bitrate index 0: the frame length is not in
    the header) at 0, and the same header again later in ``head``. metaex
    recognizes such files only to refuse them clearly."""
    if len(head) < 8 or head[0] != 0xFF or head[1] & 0xE0 != 0xE0 or head[3] & 3 == 2:
        return False
    b1, b2 = head[1], head[2]
    if (b1 >> 3) & 3 == 1 or (b1 >> 1) & 3 == 0 or b2 >> 4 != 0 or (b2 >> 2) & 3 == 3:
        return False
    pattern = bytes([0xFF, b1])
    pos = head.find(pattern, 4)
    while pos >= 0 and pos + 3 <= len(head):
        if head[pos + 2] & 0xFD == b2 & 0xFD:  # the padding bit may differ
            return True
        pos = head.find(pattern, pos + 1)
    return False


def _is_mpeg_audio(head: bytes) -> bool:
    """A valid frame header at 0 followed by a matching one right after it.

    One header alone is too easy to hit by accident (0xFFFB... is common in
    random data), so a second consistent frame is required.
    """
    if _is_free_format_mpeg(head):
        return True
    first = _mpeg_frame_length(head, 0)
    if first is None:
        return False
    if _mpeg_frame_length(head, first) is None:
        return False
    # Same version and layer (ignore the CRC bit), same sample rate.
    return (
        head[1] & 0xFE == head[first + 1] & 0xFE
        and (head[2] >> 2) & 0x3 == (head[first + 2] >> 2) & 0x3
    )


# -- TAR -------------------------------------------------------------------------

def _is_tar(head: bytes) -> bool:
    """POSIX/GNU tar: "ustar" magic at 257 and a valid header checksum."""
    if len(head) < 512 or head[257:262] != b"ustar":
        return False
    field = head[148:156].split(b"\x00", 1)[0].strip()
    if not field or any(c not in b"01234567" for c in field):
        return False
    stored = int(field, 8)
    header = head[:148] + b" " * 8 + head[156:512]
    unsigned = sum(header)
    signed = sum(b - 256 if b > 127 else b for b in header)
    return stored in (unsigned, signed)


# -- SVG -------------------------------------------------------------------------

_SVG_ROOT = re.compile(r"<(?:[A-Za-z_][\w.-]*:)?svg[\s/>]")


def _looks_like_svg(head: bytes) -> bool:
    """True if the root element within the first 4 KB is <svg>.

    Allows a BOM, an XML declaration, processing instructions, comments,
    a DOCTYPE and whitespace before it. Everything must be complete within
    the sniff window; anything else before <svg means "not SVG".
    """
    text = _decode_text(head)
    if text is None:
        return False
    pos, n = 0, len(text)
    while pos < n:
        # Every branch either returns or strictly advances pos, so this
        # loop is bounded by the length of the sniff window.
        if text[pos].isspace():
            pos += 1
        elif text.startswith("<?", pos):
            end = text.find("?>", pos + 2)
            if end < 0:
                return False
            pos = end + 2
        elif text.startswith("<!--", pos):
            end = text.find("-->", pos + 4)
            if end < 0:
                return False
            pos = end + 3
        elif text.startswith("<!DOCTYPE", pos):
            end = _doctype_end(text, pos)
            if end < 0:
                return False
            pos = end
        else:
            return _SVG_ROOT.match(text, pos) is not None
    return False


def _doctype_end(text: str, pos: int) -> int:
    """Index just past a DOCTYPE starting at pos, or -1 if not closed."""
    bracket = text.find("[", pos)
    close = text.find(">", pos)
    if close < 0:
        return -1
    if 0 <= bracket < close:  # internal subset: [ ... ]>
        end_subset = text.find("]", bracket)
        if end_subset < 0:
            return -1
        close = text.find(">", end_subset)
        if close < 0:
            return -1
    return close + 1


def _decode_text(head: bytes) -> str | None:
    if head.startswith(b"\xef\xbb\xbf"):
        return head[3:].decode("utf-8", errors="replace")
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        # Drop a trailing odd byte so a cut-off code unit doesn't error.
        body = head[: len(head) - (len(head) % 2)]
        return body.decode("utf-16", errors="replace")
    if b"\x00" in head[:64]:
        return None  # binary, or UTF-16 without BOM (not supported)
    return head.decode("utf-8", errors="replace")
