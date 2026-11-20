"""Load a file into memory, refusing anything over a size limit."""

from __future__ import annotations

import os
import stat

from metaex.core.errors import FileAccessError, FileTooLargeError

MIB = 1024 * 1024
DEFAULT_MAX_SIZE = 512 * MIB


def load_file(path: str | os.PathLike[str], max_size: int = DEFAULT_MAX_SIZE) -> bytes:
    """Return the full contents of a regular file of at most max_size bytes.

    The size is checked with stat() before any data is read, and the read
    itself is capped at max_size + 1 bytes, so a file that grows between the
    two calls is still refused rather than loaded.

    Raises FileAccessError for missing files, directories, non-regular files
    (devices, pipes; reading those could block forever) and permission
    problems, and FileTooLargeError when the limit is exceeded.
    """
    if not isinstance(max_size, int) or isinstance(max_size, bool) or max_size < 0:
        raise ValueError(f"max_size must be a non-negative int, not {max_size!r}")

    name = os.fspath(path)
    try:
        st = os.stat(name)
    except FileNotFoundError:
        raise FileAccessError(f"{name}: no such file") from None
    except PermissionError:
        raise FileAccessError(f"{name}: permission denied") from None
    except OSError as exc:
        raise FileAccessError(f"{name}: cannot access file ({_reason(exc)})") from None

    if stat.S_ISDIR(st.st_mode):
        raise FileAccessError(f"{name}: is a directory, not a file")
    if not stat.S_ISREG(st.st_mode):
        raise FileAccessError(f"{name}: not a regular file")
    if st.st_size > max_size:
        raise FileTooLargeError(_too_large(name, st.st_size, max_size))

    try:
        with open(name, "rb") as f:
            data = f.read(max_size + 1)
    except PermissionError:
        raise FileAccessError(f"{name}: permission denied") from None
    except IsADirectoryError:
        raise FileAccessError(f"{name}: is a directory, not a file") from None
    except OSError as exc:
        raise FileAccessError(f"{name}: cannot read file ({_reason(exc)})") from None

    if len(data) > max_size:
        raise FileTooLargeError(_too_large(name, None, max_size))
    return data


def format_size(n: int) -> str:
    """Human-readable size using binary units, e.g. '1.5 MiB'."""
    if n < 1024:
        return f"{n} B" if n != 1 else "1 byte"
    value = float(n)
    for unit in ("KiB", "MiB", "GiB", "TiB", "PiB"):
        value /= 1024
        if value < 1024 or unit == "PiB":
            return f"{value:.1f} {unit}"
    raise AssertionError("unreachable")


def _too_large(name: str, size: int | None, max_size: int) -> str:
    actual = f"{format_size(size)}" if size is not None else "larger than the limit"
    return (
        f"{name}: file is too large ({actual}; limit is {format_size(max_size)}). "
        f"Use --max-size to raise the limit."
    )


def _reason(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__
