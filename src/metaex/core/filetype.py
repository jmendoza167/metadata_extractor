"""Detect a file's real format from its content ("magic bytes").

Detection never trusts the file name: the name is only used to warn when
the extension doesn't match the content. Unrecognized data is reported as
the "unknown" type; detect() never raises on bad input.

``supported`` says whether the format is in metaex's scope. It does not
mean a parser for it already exists.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# How far into a file we look for an SVG root element.
SVG_SNIFF_LIMIT = 4096
# Upper bound on ftyp compatible brands we examine.
FTYP_MAX_BRANDS = 64


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


def _t(key: str, name: str, mime: str, exts: str, supported: bool) -> FileType:
    return FileType(key, name, mime, tuple(exts.split()), supported)


JPEG = _t("jpeg", "JPEG", "image/jpeg", ".jpg .jpeg .jpe .jfif", True)
PNG = _t("png", "PNG", "image/png", ".png", True)
GIF = _t("gif", "GIF", "image/gif", ".gif", True)
WEBP = _t("webp", "WebP", "image/webp", ".webp", True)
SVG = _t("svg", "SVG", "image/svg+xml", ".svg", True)
TIFF = _t("tiff", "TIFF", "image/tiff", ".tif .tiff", False)
HEIC = _t("heic", "HEIC", "image/heic", ".heic .heif .hif", False)
HEIF = _t("heif", "HEIF", "image/heif", ".heif .heic .hif", False)
AVIF = _t("avif", "AVIF", "image/avif", ".avif", False)

UNKNOWN = _t("unknown", "unknown", "application/octet-stream", "", False)


def detect(data: bytes | bytearray | memoryview, filename: str | os.PathLike[str] | None = None) -> FileType:
    """Identify the format of ``data``. Never raises for any input bytes."""
    head = bytes(data[:SVG_SNIFF_LIMIT])

    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head.startswith((b"GIF87a", b"GIF89a")):
        return GIF
    if head.startswith(b"RIFF") and len(head) >= 12:
        if head[8:12] == b"WEBP":
            return WEBP
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return TIFF
    if head[4:8] == b"ftyp":
        found = _detect_ftyp(head)
        if found is not None:
            return found
    if _looks_like_svg(head):
        return SVG
    return UNKNOWN


# -- ISO base media (ftyp) -------------------------------------------------------

_AVIF_BRANDS = {b"avif", b"avis"}
_HEIC_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs"}
_HEIF_BRANDS = {b"mif1", b"msf1"}


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
    if major in _AVIF_BRANDS:
        return AVIF
    if major in _HEIC_BRANDS:
        return HEIC
    if compatible & _AVIF_BRANDS:
        return AVIF
    if compatible & _HEIC_BRANDS:
        return HEIC
    if major in _HEIF_BRANDS or compatible & _HEIF_BRANDS:
        return HEIF
    return None


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
