"""ODS: OpenDocument spreadsheets (also .ots templates).

Everything OpenDocument formats have in common (meta.xml, settings.xml,
comments, tracked changes, pictures, macros, the ZIP container) is in
core/odf.py.
"""

from __future__ import annotations


def parse(data: bytes | bytearray | memoryview) -> dict:
    """Parse the file and return its warnings, report sections and structure.
    Raises MalformedFileError if it can't be opened at all and
    UnsupportedFormatError if it is encrypted."""
    from metaex.core.odf import parse_package

    parsed = parse_package(data, "ods")
    parsed.pop("package")
    return parsed
