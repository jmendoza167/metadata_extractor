"""xz files: .xz, .txz and .tar.xz.

xz headers hold no metadata (only the integrity check type); what
matters is what the file holds (a TAR archive, or a single file). See
core/compress.py.
"""

from __future__ import annotations

from metaex.core import compress

KIND = compress.XZ


def parse(data: bytes | bytearray | memoryview) -> dict:
    """The wrapper's metadata and, for a compressed TAR, the TAR's.
    Raises MalformedFileError if the file is damaged or would expand past
    a limit."""
    return compress.parse(data, KIND)


CAN_DEEP = True
"""strip(deep=True) also cleans the file or the files of the TAR inside."""


def strip(data: bytes | bytearray | memoryview, *, keep_orientation: bool = True,
          paranoid: bool = False, deep: bool = False) -> bytes:
    """A cleaned copy (see compress.strip()): a compressed TAR is cleaned as a
    TAR; a single file inside is copied as it is, or cleaned with ``deep``
    (``keep_orientation`` and ``paranoid`` apply to the files inside)."""
    return compress.strip(data, KIND, deep=deep, keep_orientation=keep_orientation,
                          paranoid=paranoid)


def removed_sizes(data: bytes | bytearray | memoryview) -> dict[str, int]:
    return compress.removed_sizes(data)


def strip_warnings(data: bytes, paranoid: bool = False) -> list[str]:
    return compress.strip_warnings(data)


def kept_warnings(cleaned: bytes) -> list[str]:
    return compress.kept_warnings(cleaned)


IMAGE_LABEL = compress.IMAGE_LABEL


def is_kept_by_design(section_key: str, field) -> bool:
    return compress.is_kept_by_design(section_key, field)


def verify_details(cleaned: bytes, original: bytes | None = None) -> dict:
    """The wrapper must be clean and the contents the original's (a TAR
    verified as a TAR); see compress.verify_details()."""
    return compress.verify_details(bytes(cleaned), None if original is None else bytes(original),
                                   KIND)
