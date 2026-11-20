"""Find the metaex.formats module that handles a detected file type."""

from __future__ import annotations

import importlib
import importlib.util
from types import ModuleType

from metaex.core.filetype import FileType


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
    return module is not None and callable(getattr(module, "strip", None))
