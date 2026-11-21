"""metaex strip: write a verified copy of a file with its metadata removed."""

from __future__ import annotations

import os
import secrets
import stat
import sys
from dataclasses import dataclass, field
from typing import Any

from metaex.core import filetype as ft
from metaex.core import icc
from metaex.core.errors import (CannotCleanError, MalformedFileError, MetaexError,
                                UnsupportedFormatError)
from metaex.core.generic import mark_of_the_web_section
from metaex.core.loader import DEFAULT_MAX_SIZE, format_size, load_file
from metaex.core.registry import can_strip, format_module
from metaex.core.report import format_value
from metaex.core.verify import Finding, VerifyResult, verify
from metaex.core.writer import write_verified


@dataclass
class StripOutcome:
    source: str
    output: str
    filetype: ft.FileType
    original_size: int
    output_size: int
    removed: dict[str, list[str]] = field(default_factory=dict)
    """Group name -> labels of the removed fields."""
    removed_sizes: dict[str, int] = field(default_factory=dict)
    """Group name -> bytes removed, for groups better described by size
    (data after the end of the image, extra images)."""
    kept_by_design: list[Finding] = field(default_factory=list)
    verify: VerifyResult | None = None
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    """Things kept in the output that the user may want to remove."""
    timestamps: str = ""
    """How the output's file times were set."""
    zone_identifier: str | None = None
    """Windows only: whether the output has a Zone.Identifier stream."""

    def to_dict(self) -> dict[str, Any]:
        v = self.verify
        return {
            "source": self.source,
            "output": self.output,
            "type": self.filetype.name,
            "original_size": self.original_size,
            "output_size": self.output_size,
            "removed": self.removed,
            "removed_sizes": self.removed_sizes,
            "kept_by_design": [
                {"section": f.section, "label": f.label, "value": f.value}
                for f in self.kept_by_design
            ],
            "verify": None if v is None else {
                "clean": v.clean,
                "leftovers": [str(f) for f in v.leftovers],
                "problems": v.problems,
                "image_data_match": v.image_data_match,
                "image_data_rebuilt": v.image_data_rebuilt,
                "image_sha256": v.image_sha256,
                "original_image_sha256": v.original_image_sha256,
            },
            "timestamps": self.timestamps,
            "zone_identifier": self.zone_identifier,
            "warnings": self.warnings,
            "notes": self.notes,
        }


def default_output_path(path: str, filetype: ft.FileType) -> str:
    """<name>_clean.<ext> next to the original."""
    directory, base = os.path.split(path)
    stem, ext = os.path.splitext(base)
    if not ext:
        ext = filetype.extensions[0] if filetype.extensions else ""
    return os.path.join(directory, f"{stem}_clean{ext}")


FIXED_TIME = 946684800.0  # 2000-01-01T00:00:00Z
FIXED_TIME_TEXT = "2000-01-01T00:00:00Z"


def random_output_path(path: str, filetype: ft.FileType, attempts: int = 20) -> str:
    """img_<8 random hex digits>.<ext> in the same folder, not yet existing.

    The extension is the canonical one for the detected type, not the
    original's, so the name reveals nothing about the original file.
    """
    directory = os.path.dirname(path)
    ext = filetype.extensions[0] if filetype.extensions else ""
    for _ in range(attempts):
        candidate = os.path.join(directory, f"img_{secrets.token_hex(4)}{ext}")
        if not os.path.lexists(candidate):
            return candidate
    raise MetaexError("could not find an unused random file name; no file written")


def strip_file(path: str | os.PathLike[str], output: str | os.PathLike[str] | None = None, *,
               paranoid: bool = False, keep_orientation: bool = True,
               max_size: int = DEFAULT_MAX_SIZE, in_place: bool = False,
               fixed_time: bool = False, rename: bool = False,
               rebuild_image_data: bool = False) -> StripOutcome:
    """load -> detect -> strip -> temp file -> verify -> move into place.

    With ``in_place`` the original is replaced (still via a verified temp
    file and os.replace, keeping its permission bits); the caller is
    responsible for asking the user first. The output never gets the
    original's timestamps; with ``fixed_time`` its modified and accessed
    times are set to 2000-01-01T00:00:00Z. With ``rename`` the output gets a
    random name (img_<hex>.<ext>) in the original's folder. With
    ``rebuild_image_data`` (PNG only), image data that hides extra bytes is
    decompressed and compressed again without them (identical pixels)
    instead of being refused.

    Raises (and writes nothing) for unsupported types, malformed input, an
    existing output file, or a failed verification.
    """
    source = os.fspath(path)
    data = load_file(source, max_size)
    detected = ft.detect(data, source)
    module = format_module(detected)
    if not can_strip(module):
        what = ("this file: its type was not recognized" if detected.key == ft.UNKNOWN.key
                else f"{detected.name} files yet")
        raise UnsupportedFormatError(f"metaex can't strip {what}; no file written")

    if rebuild_image_data and not getattr(module, "CAN_REBUILD_IMAGE_DATA", False):
        raise MetaexError(f"--rebuild-image-data is not available for {detected.name} files; "
                          "no file written")
    if sum((in_place, output is not None, rename)) > 1:
        raise MetaexError("choose only one of --in-place, -o and --rename")
    if in_place:
        target = source
    elif rename:
        target = random_output_path(source, detected)
    else:
        target = os.fspath(output) if output is not None else default_output_path(source, detected)
        if os.path.lexists(target):
            hint = "" if output is not None else " Use -o to choose another name."
            raise MetaexError(f"{target}: already exists; refusing to overwrite it.{hint} "
                              "No file written.")
    options = {"rebuild_image_data": True} if rebuild_image_data else {}
    try:
        cleaned = module.strip(data, keep_orientation=keep_orientation, paranoid=paranoid,
                               **options)
    except (MalformedFileError, CannotCleanError) as exc:
        raise type(exc)(f"{source}: {exc}; no file written") from None

    def check(on_disk: bytes) -> VerifyResult:
        result = verify(on_disk, data, filename=target)
        if not result.clean or result.image_data_match is not True:
            reasons = result.problems + [str(f) for f in result.leftovers]
            raise MetaexError("verification of the cleaned file failed ("
                              + "; ".join(reasons[:3]) + "); no file written")
        return result

    times = (FIXED_TIME, FIXED_TIME) if fixed_time else None
    if in_place:
        mode = stat.S_IMODE(os.stat(source).st_mode)
        result = write_verified(target, cleaned, check, overwrite=True, mode=mode, times=times)
    else:
        result = write_verified(target, cleaned, check, times=times)
    outcome = StripOutcome(source, target, detected, len(data), len(cleaned),
                           kept_by_design=result.kept_by_design, verify=result)
    outcome.removed = removed_groups(module.parse(data), result.kept_by_design)
    sizes_hook = getattr(module, "removed_sizes", None)
    if callable(sizes_hook):
        outcome.removed_sizes = {_SECTION_GROUPS.get(key, key): n
                                 for key, n in sizes_hook(data).items()}
    outcome.warnings.extend(kept_warnings(module.parse(cleaned)))
    outcome.notes.extend(detected.warnings)
    if fixed_time:
        outcome.timestamps = (f"modified and accessed set to {FIXED_TIME_TEXT}; "
                              "creation time is set by the operating system")
    else:
        outcome.timestamps = "new (not copied from the original)"
    if sys.platform == "win32":
        if mark_of_the_web_section(target) is None:
            outcome.zone_identifier = "none (no download origin attached)"
        else:
            outcome.zone_identifier = "PRESENT"
            outcome.notes.append("the output has a Zone.Identifier stream; this should not "
                                 "happen, please report it")
    return outcome


def kept_warnings(parsed_output: dict) -> list[str]:
    """Warnings about metadata the cleaned file still has by design."""
    warnings = []
    for section in parsed_output.get("sections", []):
        if section.key == "icc":
            device = icc.named_device(section)
            if device:
                warnings.append(f"ICC profile names a device ({device}); use --paranoid to "
                                "remove it (colors may shift)")
    return warnings


# -- what was removed, grouped for people --------------------------------------------

_SECTION_GROUPS = {
    "gps": "GPS location",
    "thumbnail": "Thumbnail",
    "mpf": "Extra images (MPF)",
    "trailing": "Data after end of image",
    "padding": "Data after end of image",
    "xmp": "XMP",
    "iptc": "IPTC",
    "photoshop": "Photoshop resources",
    "comments": "Comments",
    "icc": "ICC profile",
    "jfif": "JFIF",
    "adobe": "Adobe",
    "other_app": "Other APP segments",
    # PNG
    "text": "Text",
    "ai": "AI generation data",
    "time": "Modification time",
    "content_credentials": "Content Credentials (C2PA)",
    "color": "Color and display",
    "unknown": "Unknown chunks",
    "hidden_image_data": "Hidden data in image data",
    # GIF
    "plain_text": "Plain text",
    "app_extensions": "Application extensions",
}
_CAMERA = {"Make", "Model", "BodySerialNumber", "LensMake", "LensModel", "LensSerialNumber",
           "LensSpecification", "MakerNote", "CameraOwnerName", "UniqueCameraModel",
           "CameraFirmware", "ImageUniqueID"}
_AUTHOR = {"Artist", "Copyright", "ImageDescription", "UserComment", "ImageTitle",
           "Photographer", "ImageEditor", "DocumentName"}
_THUMBNAIL_FIELDS = {("jfif", "thumbnail"), ("jfif", "jfxx_thumbnail"),
                     ("photoshop", "0x040c"), ("photoshop", "0x0409")}


def group_for(section_key: str, field_key: str) -> str | None:
    """Category a removed field is reported under; None for non-metadata fields."""
    if (section_key, field_key) in _THUMBNAIL_FIELDS:
        return "Thumbnail"
    if section_key == "exif":
        name = field_key.rsplit(".", 1)[-1]
        if name == "byte_order":
            return None
        if "DateTime" in name or name.startswith(("OffsetTime", "SubSecTime")):
            return "Dates"
        if name in _CAMERA:
            return "Camera and owner"
        if "Software" in name or name in ("HostComputer",):
            return "Software"
        if name in _AUTHOR or name.startswith("XP"):
            return "Author and descriptions"
        return "Camera settings and other EXIF"
    return _SECTION_GROUPS.get(section_key, "Other")


def removed_groups(parsed: dict, kept: list[Finding]) -> dict[str, list[str]]:
    """Labels of the original's fields that are not in the cleaned file, by group.

    A field that is still there but with a different value (e.g. a PNG ICC
    profile renamed to the generic "ICC profile") counts as removed too, and
    its label says what the value was and what it became.
    """
    kept_values: dict[tuple[str, str], list[str]] = {}
    for f in kept:
        kept_values.setdefault((f.section, f.label), []).append(f.value)
    groups: dict[str, list[str]] = {}
    for section in parsed.get("sections", []):
        for f in section.fields:
            label = f.label
            values = kept_values.get((section.title, f.label))
            if values is not None:
                value = format_value(f.value)
                if value in values:
                    continue
                label = f'{f.label} "{value}" (replaced with "{values[0]}")'
            group = group_for(section.key, f.key)
            if group is not None:
                groups.setdefault(group, []).append(label)
    return groups


# -- text summary --------------------------------------------------------------------

def summary_lines(outcome: StripOutcome) -> list[str]:
    """Human-readable summary (unsanitized; the caller sanitizes and prints)."""
    lines = [f"metaex strip: {outcome.source}",
             f"Wrote {outcome.output} ({format_size(outcome.output_size)}; "
             f"original {format_size(outcome.original_size)})", ""]
    lines.append("Removed:")
    if outcome.removed:
        width = max(len(g) for g in outcome.removed)
        for group, labels in outcome.removed.items():
            if group in outcome.removed_sizes:
                lines.append(f"  {group.ljust(width)}  "
                             f"{format_size(outcome.removed_sizes[group])}")
                continue
            sample = ", ".join(labels[:4]) + (", ..." if len(labels) > 4 else "")
            noun = "field" if len(labels) == 1 else "fields"
            lines.append(f"  {group.ljust(width)}  {len(labels)} {noun}: {sample}")
    else:
        lines.append("  (no metadata found)")
    lines.append("")
    lines.append("Kept by design:")
    if outcome.kept_by_design:
        for section, values in _kept_by_section(outcome.kept_by_design).items():
            lines.append(f"  {section}: {values}")
    else:
        lines.append("  (nothing)")
    for warning in outcome.warnings:
        lines.append(f"  Warning: {warning}")
    lines.append("")
    v = outcome.verify
    if v is not None:
        status = "clean" if v.clean else "NOT CLEAN"
        lines.append(f"Verification: {status}")
        if v.image_data_match and v.image_data_rebuilt:
            lines.append("  Image data rebuilt, pixels identical (SHA-256 of the "
                         f"decompressed image data {v.image_sha256})")
        elif v.image_data_match:
            lines.append(f"  Image data unchanged (SHA-256 {v.image_sha256})")
    lines.append("")
    lines.append(f"File timestamps: {outcome.timestamps}")
    if outcome.zone_identifier is not None:
        lines.append(f"Windows Zone.Identifier stream: {outcome.zone_identifier}")
    for note in outcome.notes:
        lines.append(f"Note: {note}")
    return lines


def _kept_by_section(findings: list[Finding]) -> dict[str, str]:
    out: dict[str, list[Finding]] = {}
    for f in findings:
        out.setdefault(f.section, []).append(f)
    result = {}
    for section, items in out.items():
        if section == "ICC profile":
            desc = next((f.value for f in items if f.label == "Description"), None)
            text = "kept so colors stay correct"
            result[section] = f'{text} (description: "{desc}")' if desc else text
        elif section == "Color and display":
            result[section] = "kept so the image displays correctly: " + ", ".join(
                f.label for f in items)
        else:
            result[section] = ", ".join(f"{f.label} = {f.value}" for f in items
                                        if f.label != "Byte order")
    return result
