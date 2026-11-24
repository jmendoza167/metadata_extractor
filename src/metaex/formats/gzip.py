"""gzip files: .gz, .tgz and .tar.gz.

Each gzip member header can hold the original file name, its
modification time, the operating system, a comment and extra fields;
core/compress.py reads them and what the file holds (a TAR archive, or
a single file).
"""

from __future__ import annotations

from metaex.core import compress

KIND = compress.GZIP


def parse(data: bytes | bytearray | memoryview) -> dict:
    """The wrapper's metadata and, for a compressed TAR, the TAR's.
    Raises MalformedFileError if the file is damaged or would expand past
    a limit."""
    return compress.parse(data, KIND)
