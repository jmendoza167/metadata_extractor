"""Check that a cleaned file really contains no metadata.

The check is independent of how the file was cleaned: the file is detected
and parsed again from scratch, and every metadata section or field that is
not on the format's "kept by design" whitelist is reported as a leftover.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from metaex.core.errors import UnsupportedFormatError
from metaex.core.filetype import FileType, detect
from metaex.core.registry import can_strip, format_module
from metaex.core.report import format_value


@dataclass(frozen=True)
class Finding:
    section: str
    label: str
    value: str

    def __str__(self) -> str:
        return f"{self.section}: {self.label} = {self.value}"


@dataclass
class VerifyResult:
    filetype: FileType
    clean: bool = False
    leftovers: list[Finding] = field(default_factory=list)
    """Metadata found that is not on the whitelist."""
    kept_by_design: list[Finding] = field(default_factory=list)
    """Metadata deliberately kept (e.g. Orientation, ICC profile)."""
    problems: list[str] = field(default_factory=list)
    """Other reasons the file can't be called clean (malformed, image changed)."""
    notes: list[str] = field(default_factory=list)
    """Informational messages that don't affect the result."""
    image_data_match: bool | None = None
    """True/False when compared with an original; None if not compared."""
    image_sha256: str | None = None
    original_image_sha256: str | None = None
    image_data_rebuilt: bool = False
    """True if the image data was compressed again (PNG --rebuild-image-data);
    then image_data_match compares the decompressed data: identical pixels."""


def verify(cleaned: bytes, original: bytes | None = None,
           filename: str | None = None) -> VerifyResult:
    """Re-detect and re-parse ``cleaned`` and classify everything still in it.

    Raises UnsupportedFormatError for types metaex can't strip, and
    MalformedFileError if the file can't be parsed at all.
    """
    cleaned = bytes(cleaned)
    detected = detect(cleaned, filename)
    module = format_module(detected)
    if not can_strip(module):
        raise UnsupportedFormatError(f"metaex can't verify {detected.name} files yet")

    result = VerifyResult(filetype=detected)
    result.notes.extend(detected.warnings)
    parsed = module.parse(cleaned)
    whitelist: dict[str, set[str] | None] = getattr(module, "KEPT_BY_DESIGN", {})
    for section in parsed.get("sections", []):
        allowed = whitelist.get(section.key, set())
        for f in section.fields:
            finding = Finding(section.title, f.label, format_value(f.value))
            if allowed is None or f.key in allowed:
                result.kept_by_design.append(finding)
            else:
                result.leftovers.append(finding)
    if not parsed.get("complete", True):
        result.problems.append("the file structure is malformed, so it could not be "
                               "checked completely")
    result.notes.extend(w for w in parsed.get("warnings", []) if not w.startswith("CRITICAL"))

    # Format-specific checks: hidden bytes, structure whitelist, image data.
    details_hook = getattr(module, "verify_details", None)
    if callable(details_hook):
        details = details_hook(cleaned, None if original is None else bytes(original))
        result.leftovers.extend(Finding(*item) for item in details.get("leftovers", []))
        result.problems.extend(details.get("problems", []))
        result.image_sha256 = details.get("image_sha256")
        result.original_image_sha256 = details.get("original_image_sha256")
        result.image_data_match = details.get("image_data_match")
        result.image_data_rebuilt = bool(details.get("image_data_rebuilt", False))

    result.clean = not result.leftovers and not result.problems
    return result
