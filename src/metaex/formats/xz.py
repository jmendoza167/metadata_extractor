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
