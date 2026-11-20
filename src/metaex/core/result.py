"""Plain data model for what metaex reports: sections made of fields."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Field:
    key: str
    """Stable machine-readable name, e.g. "size_bytes"."""
    label: str
    """Human-readable name, e.g. "Size (bytes)"."""
    value: Any
    sensitive: bool = False
    """True if the value can reveal something about a person or place."""
    note: str | None = None
    """Short explanation shown next to the value."""


@dataclass
class Section:
    key: str
    title: str
    fields: list[Field] = field(default_factory=list)

    def add(self, key: str, label: str, value: Any, *, sensitive: bool = False,
            note: str | None = None) -> Field:
        f = Field(key, label, value, sensitive=sensitive, note=note)
        self.fields.append(f)
        return f

    def get(self, key: str) -> Field | None:
        for f in self.fields:
            if f.key == key:
                return f
        return None
