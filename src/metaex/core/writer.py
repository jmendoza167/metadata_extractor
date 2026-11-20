"""Write output files without ever overwriting an existing file."""

from __future__ import annotations

import os

from metaex.core.errors import FileAccessError


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
