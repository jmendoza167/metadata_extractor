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
