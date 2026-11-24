"""bzip2 files: .bz2, .tbz2 and .tar.bz2.

bzip2 headers hold no metadata (only the block size); what matters is
what the file holds (a TAR archive, or a single file) and anything
hidden after its streams. See core/compress.py.
"""

from __future__ import annotations

from metaex.core import compress

KIND = compress.BZIP2


def parse(data: bytes | bytearray | memoryview) -> dict:
    """The wrapper's metadata and, for a compressed TAR, the TAR's.
    Raises MalformedFileError if the file is damaged or would expand past
    a limit."""
    return compress.parse(data, KIND)


def strip(data: bytes | bytearray | memoryview, *, keep_orientation: bool = True,
          paranoid: bool = False) -> bytes:
    """A cleaned copy (see compress.rewrap()): a compressed TAR is cleaned as a
    TAR; a single file inside is copied as it is. ``keep_orientation`` and
    ``paranoid`` apply to files inside, which are not cleaned here."""
    return compress.strip(data, KIND)


def removed_sizes(data: bytes | bytearray | memoryview) -> dict[str, int]:
    return compress.removed_sizes(data)


def strip_warnings(data: bytes, paranoid: bool = False) -> list[str]:
    return compress.strip_warnings(data)


def kept_warnings(cleaned: bytes) -> list[str]:
    return compress.kept_warnings(cleaned)
