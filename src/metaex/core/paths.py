"""Find absolute local file paths in text, and the user names they reveal.

Used for SVG attributes (export paths, linked images) and Office
relationships and properties (attached templates, linked files, the
folder a workbook was saved in).
"""

from __future__ import annotations

import re
import urllib.parse

_PATH_START = r"""(?:^|(?<=[\s"'(,;=]))"""
_LOCAL_PATH = re.compile(_PATH_START + r"""(
      file:/[^\s"')]*
    | [A-Za-z]:[\\/][^"')]*
    | \\\\[^\\\s"')]+\\[^"')]*
    | /(?:home|Users|root|var/folders|private/var|mnt|media|tmp)/[^"')]*
    )""", re.VERBOSE)
_USER_NAME = re.compile(r"(?i)(?:[a-z]:[\\/]+(?:users|documents and settings)[\\/]+|"
                        r"/home/|/users/)([^\\/\"']+)")
_UNC_SERVER = re.compile(r"^(?:file:)?[\\/]{2,}([^\\/\s]+)[\\/]")


def local_paths(value: str) -> list[str]:
    """Absolute local file paths (Windows, UNC, Unix home folders, file: URLs)."""
    return [m.group(1).rstrip() for m in _LOCAL_PATH.finditer(value)]


def user_name(path: str) -> str | None:
    """The user name in a path inside a user folder (C:\\Users\\<name>, /home/<name>)."""
    m = _USER_NAME.search(urllib.parse.unquote(path))
    return m.group(1) if m else None


def server_name(path: str) -> str | None:
    """The server of a network (UNC) path: \\\\<server>\\share or file://<server>/share."""
    m = _UNC_SERVER.match(urllib.parse.unquote(path))
    if m is None or m.group(1).lower() == "localhost" or ":" in m.group(1):
        return None
    return m.group(1)
