"""metaex formats: the formats metaex handles, what strip removes, and what it refuses."""

from __future__ import annotations

import textwrap
from typing import Any

from metaex.core import filetype as ft
from metaex.core.registry import STRIPPABLE

# (category, type, what strip removes in one line). Every format in
# registry.STRIPPABLE has exactly one entry (a test checks this).
SUPPORTED: tuple[tuple[str, ft.FileType, str], ...] = (
    ("Images", ft.JPEG, "EXIF (camera, dates, GPS), XMP, IPTC/Photoshop, thumbnails, MPF extra "
                        "images, comments, unknown segments, data after the image"),
    ("Images", ft.PNG, "text chunks (including AI prompts), XMP, EXIF, tIME, Content "
                       "Credentials, unknown chunks, data after the image"),
    ("Images", ft.GIF, "comments, plain text, XMP and other application extensions, data after "
                       "the image"),
    ("Images", ft.WEBP, "EXIF, XMP, unknown chunks, data after the image"),
    ("Images", ft.SVG, "<metadata>, comments, processing instructions, editor data, local file "
                       "paths; embedded images are stripped by their own rules"),
    ("Office documents", ft.DOCX, "document, application and custom properties, authors, "
                                  "revision IDs, thumbnail, printer settings, unused parts"),
    ("Office documents", ft.XLSX, "document, application and custom properties, authors, "
                                  "workbook history, thumbnail, printer settings, unused parts"),
    ("Office documents", ft.PPTX, "document, application and custom properties, authors, "
                                  "thumbnail, printer settings, unused parts"),
    ("Office documents", ft.ODT, "document and custom properties, application settings, "
                                 "authors, signatures, thumbnail"),
    ("Office documents", ft.ODS, "document and custom properties, application settings, "
                                 "authors, signatures, thumbnail"),
    ("Office documents", ft.ODP, "document and custom properties, application settings, "
                                 "authors, signatures, thumbnail"),
    ("Audio", ft.MP3, "ID3v2, ID3v1, APE and Lyrics3 tags (pictures included), hidden data "
                      "between frames"),
    ("Audio", ft.WAV, "RIFF INFO, ID3, bext, iXML, axml, cue labels and every other chunk "
                      "except the audio"),
    ("Audio", ft.FLAC, "Vorbis comments, pictures, application blocks, padding, ID3 and APE "
                       "tags"),
    ("Archives", ft.ZIP, "entry times, comments, extra fields, operating system junk files; "
                         "with --deep, the metadata of the files inside"),
    ("Archives", ft.TAR, "entry times, owners, extended attributes, operating system junk "
                         "files; with --deep, the metadata of the files inside"),
    ("Archives", ft.GZIP, "the gzip header (name, time, comment), hidden data after the "
                          "compressed data; with --deep, the metadata inside"),
    ("Archives", ft.BZIP2, "hidden data after the compressed data; with --deep, the metadata "
                           "inside"),
    ("Archives", ft.XZ, "hidden data after the compressed data; with --deep, the metadata "
                        "inside"),
)

NOT_YET = "not supported yet"

# Variants of supported formats that their format module refuses too.
REFUSED_VARIANTS: tuple[str, ...] = (
    "password-protected Office and OpenDocument files (remove the password first)",
    '"Strict Open XML" documents and binary Excel workbooks (.xlsb)',
    'AAC audio in a file named .mp3, and "free-format" MP3',
    "signed ZIP archives (cleaning would break the signature) and ZIP archives with "
    "encrypted entries",
    "TAR archives with sparse files",
)


def all_types() -> list[ft.FileType]:
    """Every file type metaex recognizes, in the order core/filetype.py defines them."""
    seen: dict[str, ft.FileType] = {}
    for value in vars(ft).values():
        if isinstance(value, ft.FileType) and value.key not in seen:
            seen[value.key] = value
    return list(seen.values())


def refused_types() -> list[tuple[ft.FileType, str]]:
    """Recognized types metaex refuses to strip, each with the reason."""
    return [(t, ft.refusal(t) or NOT_YET) for t in all_types()
            if t.key not in STRIPPABLE and t.key != ft.UNKNOWN.key]


def formats_to_dict() -> dict[str, Any]:
    return {
        "supported": [{"key": t.key, "name": t.name, "category": category,
                       "extensions": list(t.extensions), "mime": t.mime,
                       "strip_removes": removes}
                      for category, t, removes in SUPPORTED],
        "refused": [{"key": t.key, "name": t.name, "extensions": list(t.extensions),
                     "reason": reason}
                    for t, reason in refused_types()],
        "refused_variants": list(REFUSED_VARIANTS),
    }


def formats_lines(width: int = 100) -> list[str]:
    lines = ["Supported formats (show, risk, strip and verify):"]
    category = None
    for cat, t, removes in SUPPORTED:
        if cat != category:
            category = cat
            lines += ["", f"{cat}:"]
        lines.append(f"  {t.name}  {' '.join(t.extensions)}")
        lines += textwrap.wrap(f"strip removes: {removes}", width=width,
                               initial_indent="    ", subsequent_indent="      ")
    lines += ["", "Refused (strip and verify refuse them; show and risk only list file "
                  "system information):", ""]
    for t, reason in refused_types():
        exts = f" ({' '.join(t.extensions)})" if t.extensions else ""
        lines += textwrap.wrap(f"{t.name}{exts}: {reason}", width=width,
                               initial_indent="  ", subsequent_indent="      ")
    lines += ["", "Also refused:"]
    for variant in REFUSED_VARIANTS:
        lines += textwrap.wrap(variant, width=width, initial_indent="  - ",
                               subsequent_indent="    ")
    lines += ["", "Files of any other type are refused the same way."]
    return lines
