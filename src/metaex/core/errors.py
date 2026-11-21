"""Exception types raised by metaex.

Everything metaex raises on purpose derives from MetaexError, so the CLI can
turn it into a one-line message instead of a traceback.
"""


class MetaexError(Exception):
    """Base class for all errors metaex reports to the user."""


class MalformedFileError(MetaexError):
    """The file's contents are truncated, inconsistent or otherwise invalid."""


class HiddenDataError(MetaexError):
    """The file hides data that can't be removed by copying the image data
    byte-for-byte (e.g. extra bytes inside a PNG's compressed image data)."""


class UnsupportedFormatError(MetaexError):
    """The file is of a format metaex does not handle (and will not touch)."""


class FileTooLargeError(MetaexError):
    """The file exceeds the configured size limit."""


class FileAccessError(MetaexError):
    """The file could not be opened: missing, a directory, no permission..."""
