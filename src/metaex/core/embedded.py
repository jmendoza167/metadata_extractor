"""Embedded pictures (audio cover art and the like), read with metaex's own
image parsers so that their metadata is reported too: a cover photo with
GPS coordinates is a GPS finding like any other photo.
"""

from __future__ import annotations

from dataclasses import dataclass

from metaex.core.errors import MalformedFileError, UnsupportedFormatError
from metaex.core.result import Section

IMAGE_KEYS = ("jpeg", "png", "gif", "webp", "svg")
MAX_PICTURES = 100
"""Pictures examined per file; the rest are listed as not examined."""


@dataclass
class Picture:
    name: str
    """How the picture is referred to, e.g. "APIC 1" or "PICTURE block 2"."""
    data: bytes
    description: str = ""
    """What the container says about it (type, MIME type, description)."""


def picture_sections(pictures: list[Picture], warnings: list[str],
                     listing_key: str = "pictures",
                     listing_title: str = "Embedded pictures") -> list[Section]:
    """A list of the pictures, then the metadata sections of each one found
    with its own format's parser (titles prefixed with the picture's name)."""
    from metaex.core import filetype
    from metaex.core.registry import format_module

    listing = Section(listing_key, listing_title)
    inner: list[Section] = []
    for n, pic in enumerate(pictures):
        detected = filetype.detect(pic.data)
        kind = detected.name if detected.key != filetype.UNKNOWN.key else "unrecognized data"
        text = f"{kind}, {len(pic.data)} bytes" + (f"; {pic.description}" if pic.description
                                                   else "")
        key = f"picture_{n + 1}"
        if n >= MAX_PICTURES:
            listing.add(key, pic.name, text + " (not examined: too many pictures)",
                        sensitive=True)
            continue
        module = format_module(detected) if detected.key in IMAGE_KEYS else None
        if module is None:
            listing.add(key, pic.name, text + " (not examined: not an image metaex reads)",
                        sensitive=True)
            continue
        try:
            parsed = module.parse(pic.data)
        except (MalformedFileError, UnsupportedFormatError) as exc:
            listing.add(key, pic.name, text + f" (not examined: {exc})", sensitive=True)
            warnings.append(f"{pic.name} ({kind}) could not be examined: {exc}")
            continue
        complete = parsed.get("complete", True)
        listing.add(key, pic.name, text if complete else text + " (malformed: partly examined)",
                    sensitive=True)
        prefix = f"{pic.name} ({kind})"
        for w in parsed.get("warnings", []):
            if w.startswith("CRITICAL: "):
                warnings.append(f"CRITICAL: {prefix}: {w[len('CRITICAL: '):]}")
            else:
                warnings.append(f"{prefix}: {w}")
        for section in parsed.get("sections", []):
            inner.append(Section(section.key, f"{prefix}: {section.title}", section.fields))
    return ([listing] if listing.fields else []) + inner
