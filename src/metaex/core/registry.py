"""Find the metaex.formats module that handles a detected file type."""

from __future__ import annotations

import importlib
import importlib.util
from types import ModuleType

from metaex.core.filetype import FileType

# Formats whose strip() and verification are finished and enabled. A format
# module can have a strip() under development without being offered here.
STRIPPABLE = frozenset({"jpeg"})


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
