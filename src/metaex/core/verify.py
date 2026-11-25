"""Check that a cleaned file really contains no metadata.

The check is independent of how the file was cleaned: the file is detected
and parsed again from scratch, and every metadata section or field that is
not on the format's "kept by design" whitelist is reported as a leftover.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from metaex.core.errors import UnsupportedFormatError
from metaex.core.filetype import FileType, detect
from metaex.core.registry import can_strip, format_module, unsupported_message
from metaex.core.report import format_value
from metaex.core.result import Field


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
    image_label: str = "Image data"
    """What image_sha256 covers: "Image data", or "Drawing" for SVG."""
    image_data_rebuilt: bool = False
    """True if the image data was compressed again (PNG --rebuild-image-data);
    then image_data_match compares the decompressed data: identical pixels."""
    inner_leftovers: list[Finding] = field(default_factory=list)
    """Archives: the leftovers that belong to files inside (also in ``leftovers``)."""
    inner_files: list[str] = field(default_factory=list)
    """Archives: the files inside that still contain metadata."""


def verdict(result: VerifyResult) -> str:
    """The one-line verdict on a file, worded the same by strip and verify:
    "CLEAN", "NOT CLEAN", or for an archive whose own metadata is clean
    while files inside still hold theirs, "NOT CLEAN: archive metadata
    clean; N file(s) inside still contain metadata (not cleaned, use
    --deep)"."""
    if result.clean:
        return "CLEAN"
    only_inside = (result.inner_files and not result.problems
                   and len(result.leftovers) == len(result.inner_leftovers))
    if only_inside:
        return (f"NOT CLEAN: archive metadata clean; {len(result.inner_files)} file(s) inside "
                "still contain metadata (not cleaned, use --deep)")
    return "NOT CLEAN"


def kept_rule(module) -> Callable[[str, Field], bool]:
    """(section key, field) -> True if a cleaned file of this format may keep
    the field: the format's is_kept_by_design(), which can decide by value
    (Office: authors may stay as "Author"), else its KEPT_BY_DESIGN
    whitelist (None for a section means all of its fields)."""
    hook = getattr(module, "is_kept_by_design", None)
    if callable(hook):
        return hook
    whitelist: dict[str, set[str] | None] = getattr(module, "KEPT_BY_DESIGN", {})

    def kept(section_key: str, f: Field) -> bool:
        allowed = whitelist.get(section_key, set())
        return allowed is None or f.key in allowed

    return kept


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
        prefix = f"{filename}: " if filename is not None else ""
        raise UnsupportedFormatError(prefix + unsupported_message(detected))

    result = VerifyResult(filetype=detected)
    result.notes.extend(detected.warnings)
    parsed = module.parse(cleaned)
    rule = kept_rule(module)
    for section in parsed.get("sections", []):
        for f in section.fields:
            finding = Finding(section.title, f.label, format_value(f.value))
            # Files inside an archive are judged by their own format's rules.
            kept = section.kept(f) if section.kept is not None else rule(section.key, f)
            if kept:
                result.kept_by_design.append(finding)
            else:
                result.leftovers.append(finding)
                if section.kept is not None:  # a file inside an archive
                    result.inner_leftovers.append(finding)
    result.inner_files = [f.path for f in parsed.get("inner", []) if f.leftovers]
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
        result.notes.extend(details.get("notes", []))
        result.image_sha256 = details.get("image_sha256")
        result.original_image_sha256 = details.get("original_image_sha256")
        result.image_data_match = details.get("image_data_match")
        result.image_data_rebuilt = bool(details.get("image_data_rebuilt", False))
        result.image_label = details.get("image_label", result.image_label)

    result.clean = not result.leftovers and not result.problems
    return result
