"""Find the metaex.formats module that handles a detected file type."""

from __future__ import annotations

import importlib
import importlib.util
from types import ModuleType

from metaex.core.filetype import UNKNOWN, FileType, refusal

# Formats whose strip() and verification are finished and enabled. A format
# module can have a strip() under development without being offered here.
STRIPPABLE = frozenset({"jpeg", "png", "gif", "webp", "svg", "docx", "xlsx", "pptx", "odt",
                        "ods", "odp", "mp3", "wav", "flac", "zip", "tar", "gzip", "bzip2", "xz"})


def format_module(filetype: FileType) -> ModuleType | None:
    """The metaex.formats module that can parse this type, or None."""
    if not filetype.supported or not filetype.key.isidentifier():
        return None
    name = f"metaex.formats.{filetype.key}"
    if importlib.util.find_spec(name) is None:
        return None
    module = importlib.import_module(name)
    return module if callable(getattr(module, "parse", None)) else None


def can_strip(module: ModuleType | None) -> bool:
    """True if metaex strips (and verifies) this format."""
    return (module is not None
            and module.__name__.rsplit(".", 1)[-1] in STRIPPABLE
            and callable(getattr(module, "strip", None)))


FORMATS_HINT = 'run "metaex formats" to see the supported formats'


def unsupported_message(filetype: FileType) -> str:
    """The one message every command gives for a format metaex doesn't
    handle: the detected type, that it isn't supported, why (for types with
    a specific reason) and how to see the supported formats."""
    kind = "unknown (not recognized)" if filetype.key == UNKNOWN.key else filetype.name
    text = f"detected type: {kind}. metaex doesn't support this format yet"
    reason = refusal(filetype)
    if reason is not None:
        text += f": {reason}"
    return f"{text}. {FORMATS_HINT[0].upper()}{FORMATS_HINT[1:]}"
