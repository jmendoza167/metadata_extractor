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


CAN_REMOVE_COMMENTS = True


def strip(data: bytes | bytearray | memoryview, *, keep_orientation: bool = True,
          paranoid: bool = False, remove_comments: bool = False) -> bytes:
    """Return a cleaned copy (see core/odf.py strip_package): meta.xml
    emptied, printer and rsids removed, thumbnail and user interface
    settings removed, comment and change authors "Author", pictures cleaned.
    ``remove_comments`` removes the comments completely."""
    from metaex.core.odf import strip_package

    return strip_package(data, "ods", keep_orientation=keep_orientation, paranoid=paranoid,
                         remove_comments=remove_comments)


def kept_warnings(cleaned: bytes) -> list[str]:
    """What a cleaned document still contains that the user should know about."""
    from metaex.core.odf import kept_warnings as warnings_for, open_odf

    return warnings_for(open_odf(cleaned))


IMAGE_LABEL = "Document content"


def is_kept_by_design(section_key: str, field) -> bool:
    from metaex.core.odf_verify import kept_by_design

    return kept_by_design(section_key, field)


def verify_details(cleaned: bytes, original: bytes | None = None) -> dict:
    """See core/odf_verify.py."""
    from metaex.core.odf import kept_warnings as warnings_for
    from metaex.core.odf_verify import verify_package

    return verify_package(bytes(cleaned), None if original is None else bytes(original),
                          "ods", warnings_for)
