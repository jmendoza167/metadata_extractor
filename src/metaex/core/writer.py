"""Write output files safely: never clobber by accident, never leave partial files."""

from __future__ import annotations

import os
import tempfile
from typing import Callable, TypeVar

from metaex.core.errors import FileAccessError

T = TypeVar("T")


def write_new_file(path: str | os.PathLike[str], data: bytes) -> None:
    """Create ``path`` and write ``data``; refuse if anything already exists there.

    The file is opened in exclusive-create mode ("xb"), so the check and the
    creation are a single atomic step: there is no window in which another
    file could appear and be clobbered.
    """
    name = os.fspath(path)
    try:
        with open(name, "xb") as f:
            f.write(data)
    except FileExistsError:
        raise FileAccessError(f"{name}: already exists; refusing to overwrite it") from None
    except IsADirectoryError:
        raise FileAccessError(f"{name}: is a directory") from None
    except PermissionError:
        raise FileAccessError(f"{name}: permission denied") from None
    except OSError as exc:
        raise FileAccessError(f"{name}: cannot write file ({exc.strerror or exc})") from None


def write_verified(dest: str | os.PathLike[str], data: bytes, check: Callable[[bytes], T], *,
                   overwrite: bool = False, mode: int | None = None) -> T:
    """Write ``data`` to ``dest`` through a verified temp file.

    1. The data is written to a temp file in the destination folder and
       flushed to disk.
    2. The temp file is read back and passed to ``check``, which must raise
       if the bytes are not acceptable.
    3. Only then is the temp file moved into place: with ``overwrite`` by
       os.replace(), otherwise without ever replacing an existing file.

    On any failure the temp file is deleted, so nothing is left behind.
    ``mode`` sets the permission bits (default: as for any new file).
    Returns whatever ``check`` returned.
    """
    target = os.fspath(dest)
    directory = os.path.dirname(os.path.abspath(target))
    try:
        fd, tmp = tempfile.mkstemp(prefix=".metaex-", suffix=".tmp", dir=directory)
    except FileNotFoundError:
        raise FileAccessError(f"{directory}: no such directory; nothing written") from None
    except PermissionError:
        raise FileAccessError(f"{directory}: permission denied; nothing written") from None
    except OSError as exc:
        raise FileAccessError(f"{directory}: cannot create a file there "
                              f"({exc.strerror or exc}); nothing written") from None
    pending: str | None = tmp
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode if mode is not None else _default_mode())
        with open(tmp, "rb") as f:
            on_disk = f.read()
        if on_disk != data:
            raise FileAccessError(f"{target}: data read back from disk differs; nothing written")
        result = check(on_disk)
        if overwrite:
            os.replace(tmp, target)
        else:
            _move_without_clobbering(tmp, target)
        pending = None
        return result
    except OSError as exc:
        raise FileAccessError(f"{target}: cannot write file ({exc.strerror or exc}); "
                              "nothing written") from None
    finally:
        if pending is not None:
            try:
                os.unlink(pending)
            except OSError:
                pass


def _move_without_clobbering(tmp: str, target: str) -> None:
    """Move tmp to target; fail if target exists (atomic where hard links work)."""
    try:
        os.link(tmp, target)
    except FileExistsError:
        raise FileAccessError(f"{target}: already exists; refusing to overwrite it; "
                              "nothing written") from None
    except OSError:
        # File system without hard links: check, then replace (small race window).
        if os.path.lexists(target):
            raise FileAccessError(f"{target}: already exists; refusing to overwrite it; "
                                  "nothing written") from None
        os.replace(tmp, target)
        return
    os.unlink(tmp)


def _default_mode() -> int:
    """Permission bits a newly created file normally gets (0o666 minus the umask)."""
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask
