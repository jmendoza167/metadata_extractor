"""metaex strip: write a verified copy of a file with its metadata removed."""

from __future__ import annotations

import os
import secrets
import stat
import sys
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any

from metaex.core import filetype as ft
from metaex.core import icc
from metaex.core.archive import DeepSession, deep_cleaning, deep_warnings, scan_inner, some
from metaex.core.errors import (CannotCleanError, MalformedFileError, MetaexError,
                                UnsupportedFormatError)
from metaex.core.generic import mark_of_the_web_section
from metaex.core.loader import DEFAULT_MAX_SIZE, format_size, load_file
from metaex.core.registry import can_strip, format_module
from metaex.core.report import CRITICAL_PREFIX, format_value
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


COMPRESSED_KEYS = ("gzip", "bzip2", "xz")


def _split_name(base: str, filetype: ft.FileType) -> tuple[str, str]:
    """(stem, extension) of a file name; a compressed file keeps both its
    extensions (backup.tar.gz -> backup, .tar.gz; photo.jpg.gz -> photo, .jpg.gz)."""
    stem, ext = os.path.splitext(base)
    if filetype.key in COMPRESSED_KEYS and ext:
        inner_stem, inner_ext = os.path.splitext(stem)
        if inner_ext and inner_stem:
            return inner_stem, inner_ext + ext
    return stem, ext


def default_output_path(path: str, filetype: ft.FileType) -> str:
    """<name>_clean.<ext> next to the original (backup.tar.gz -> backup_clean.tar.gz)."""
    directory, base = os.path.split(path)
    stem, ext = _split_name(base, filetype)
    if not ext:
        ext = filetype.extensions[0] if filetype.extensions else ""
    return os.path.join(directory, f"{stem}_clean{ext}")


FIXED_TIME = 946684800.0  # 2000-01-01T00:00:00Z
FIXED_TIME_TEXT = "2000-01-01T00:00:00Z"


def random_output_path(path: str, filetype: ft.FileType, attempts: int = 20) -> str:
    """img_<8 random hex digits>.<ext> in the same folder, not yet existing.

    The extension is the canonical one for the detected type, not the
    original's, so the name reveals nothing about the original file (a
    compressed TAR keeps ".tar" in front: img_<hex>.tar.gz).
    """
    directory = os.path.dirname(path)
    ext = filetype.extensions[0] if filetype.extensions else ""
    if _split_name(os.path.basename(path), filetype)[1].lower().startswith(".tar."):
        ext = ".tar" + ext
    for _ in range(attempts):
        candidate = os.path.join(directory, f"img_{secrets.token_hex(4)}{ext}")
        if not os.path.lexists(candidate):
            return candidate
    raise MetaexError("could not find an unused random file name; no file written")


def strip_file(path: str | os.PathLike[str], output: str | os.PathLike[str] | None = None, *,
               paranoid: bool = False, keep_orientation: bool = True,
               max_size: int = DEFAULT_MAX_SIZE, in_place: bool = False,
               fixed_time: bool = False, rename: bool = False,
               rebuild_image_data: bool = False, remove_comments: bool = False,
               deep: bool = False) -> StripOutcome:
    """load -> detect -> strip -> temp file -> verify -> move into place.

    With ``in_place`` the original is replaced (still via a verified temp
    file and os.replace, keeping its permission bits); the caller is
    responsible for asking the user first. The output never gets the
    original's timestamps; with ``fixed_time`` its modified and accessed
    times are set to 2000-01-01T00:00:00Z. With ``rename`` the output gets a
    random name (img_<hex>.<ext>) in the original's folder. With
    ``rebuild_image_data`` (PNG only), image data that hides extra bytes is
    decompressed and compressed again without them (identical pixels)
    instead of being refused. With ``remove_comments`` (Office documents)
    comments are removed completely instead of being kept with "Author" as
    their author. With ``deep`` (archives) the files inside are cleaned too,
    each with its own format's stripper, and verified.

    Raises (and writes nothing) for unsupported types, malformed input, an
    existing output file, or a failed verification.
    """
    source = os.fspath(path)
    data = load_file(source, max_size)
    detected = ft.detect(data, source)
    module = format_module(detected)
    if not can_strip(module):
        reason = ft.refusal(detected)
        if reason is not None:
            raise UnsupportedFormatError(f"metaex can't strip this file: {reason}; "
                                         "no file written")
        what = ("this file: its type was not recognized" if detected.key == ft.UNKNOWN.key
                else f"{detected.name} files yet")
        raise UnsupportedFormatError(f"metaex can't strip {what}; no file written")

    if rebuild_image_data and not getattr(module, "CAN_REBUILD_IMAGE_DATA", False):
        raise MetaexError(f"--rebuild-image-data is not available for {detected.name} files; "
                          "no file written")
    if remove_comments and not getattr(module, "CAN_REMOVE_COMMENTS", False):
        raise MetaexError(f"--remove-comments is not available for {detected.name} files; "
                          "no file written")
    if deep and not getattr(module, "CAN_DEEP", False):
        raise MetaexError(f"--deep is only for archives (ZIP, TAR, gzip, bzip2, xz), not "
                          f"{detected.name} files; no file written")
    with ExitStack() as stack:
        session = stack.enter_context(deep_cleaning()) if deep else None
        return _strip(source, data, detected, module, output, session,
                      keep_orientation=keep_orientation, paranoid=paranoid, in_place=in_place,
                      fixed_time=fixed_time, rename=rename,
                      rebuild_image_data=rebuild_image_data, remove_comments=remove_comments)


def _strip(source: str, data: bytes, detected: ft.FileType, module, output,
           session: DeepSession | None, *, keep_orientation: bool, paranoid: bool,
           in_place: bool, fixed_time: bool, rename: bool, rebuild_image_data: bool,
           remove_comments: bool) -> StripOutcome:
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
    options: dict[str, bool] = {}
    if rebuild_image_data:
        options["rebuild_image_data"] = True
    if remove_comments:
        options["remove_comments"] = True
    if session is not None:
        options["deep"] = True
    try:
        cleaned = module.strip(data, keep_orientation=keep_orientation, paranoid=paranoid,
                               **options)
    except (MalformedFileError, CannotCleanError, UnsupportedFormatError) as exc:
        raise type(exc)(f"{source}: {exc}; no file written") from None

    def check(on_disk: bytes) -> VerifyResult:
        # An archive is checked as a container here: files inside that were
        # not cleaned are listed in a warning (see kept_warnings), not refused.
        with scan_inner(False):
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
    if session is None:
        with scan_inner(False):
            outcome.removed = removed_groups(module.parse(data), result.kept_by_design)
    else:
        # What disappeared, files inside included.
        remaining = [Finding(s.title, f.label, format_value(f.value))
                     for s in module.parse(cleaned).get("sections", []) for f in s.fields]
        outcome.removed = removed_groups(module.parse(data), remaining)
    sizes_hook = getattr(module, "removed_sizes", None)
    if callable(sizes_hook):
        outcome.removed_sizes = {_SECTION_GROUPS.get(key, key): n
                                 for key, n in sizes_hook(data).items()}
    with scan_inner(False):  # files inside: see the archive's kept_warnings
        outcome.warnings.extend(kept_warnings(module.parse(cleaned)))
    if session is not None:
        outcome.warnings.extend(deep_warnings(session))
        if session.cleaned:
            outcome.notes.append(f"--deep cleaned {len(session.cleaned)} file(s) inside: "
                                 + some([f"{p} ({kind})" for p, kind in session.cleaned]))
    option_warnings = getattr(module, "strip_warnings", None)
    if callable(option_warnings):  # e.g. MP3: what --paranoid took away
        outcome.warnings.extend(option_warnings(data, paranoid=paranoid))
    format_warnings = getattr(module, "kept_warnings", None)
    if callable(format_warnings):  # e.g. SVG: kept <title>, scripts, external links
        outcome.warnings.extend(format_warnings(cleaned))
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
    # SVG
    "doctype": "DOCTYPE",
    "svg_metadata": "Metadata",
    "title_desc": "Title and description",
    "processing_instructions": "Processing instructions",
    "editor": "Editor data",
    "local_paths": "Local file paths",
    "embedded_images": "Embedded images",
    # Office documents
    "office_core": "Document properties",
    "office_app": "Application properties",
    "office_custom": "Custom properties",
    "office_labels": "Sensitivity labels",
    "office_custom_xml": "Custom XML",
    "docx_variables": "Document variables",
    "office_comments": "Comment authors and dates",
    "office_people": "People",
    "tracked_changes": "Revision authors and dates",
    "revision_ids": "Revision IDs",
    "office_thumbnail": "Thumbnail",
    "printer_settings": "Printer settings",
    "xlsx_workbook": "Workbook history",
    "office_hidden_parts": "Unused parts",
    "zip_hidden": "ZIP container",
    "zip_comment": "ZIP container",
    "zip_extra": "ZIP container",
    "zip_entries": "ZIP container",
    # OpenDocument
    "odf_meta": "Document properties",
    "odf_user_defined": "Custom properties",
    "odf_settings": "Application settings",
    "odf_configurations": "User interface settings",
    "odf_signatures": "Digital signatures",
    # Audio
    "id3v2": "ID3v2 tags",
    "id3v1": "ID3v1 tag",
    "ape": "APE tag",
    "lyrics3": "Lyrics3 tag",
    "audio_pictures": "Embedded pictures and objects",
    "hidden_audio_data": "Hidden data in the audio stream",
    "hidden_tag_data": "Hidden data in tags",
    "audio_padding": "Padding",
    "encoder_info": "Encoder information frame",
    "wav_info": "RIFF INFO",
    "wav_bext": "Broadcast WAV (bext)",
    "wav_ixml": "iXML",
    "wav_axml": "axml",
    "wav_adtl": "Cue point labels",
    "wav_cue": "Cue points",
    "wav_smpl": "Sampler loops",
    "hidden_chunk_data": "Hidden data in chunks",
    "audio_trailing": "Data after the end of the audio",
    "vorbis_comment": "Vorbis comments",
    "flac_application": "Application blocks",
    "flac_cuesheet": "Cue sheet",
    "hidden_block_data": "Hidden data in metadata blocks",
    # Archives
    "archive_hidden": "Hidden data in the archive",
    "archive_comments": "Comments",
    "archive_times": "Entry times",
    "archive_owners": "Owners",
    "archive_system": "System and permissions",
    "archive_junk": "Operating system files",
    "archive_xattrs": "Extended attributes",
    "archive_extra": "Other entry information",
    "archive_links": "Link targets",
    "archive_paths": "Names",
}
_CAMERA = {"Make", "Model", "BodySerialNumber", "LensMake", "LensModel", "LensSerialNumber",
           "LensSpecification", "MakerNote", "CameraOwnerName", "UniqueCameraModel",
           "CameraFirmware", "ImageUniqueID"}
_AUTHOR = {"Artist", "Copyright", "ImageDescription", "UserComment", "ImageTitle",
           "Photographer", "ImageEditor", "DocumentName"}
_NOT_METADATA = {"archive_contents", "compress_contents", "archive_files"}
"""What an archive contains (entry counts, sizes): not metadata, and it
changes when junk files are removed."""
_THUMBNAIL_FIELDS = {("jfif", "thumbnail"), ("jfif", "jfxx_thumbnail"),
                     ("photoshop", "0x040c"), ("photoshop", "0x0409")}


def group_for(section_key: str, field_key: str) -> str | None:
    """Category a removed field is reported under; None for non-metadata fields."""
    if section_key in _NOT_METADATA:
        return None
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
    critical = [w for w in outcome.warnings if w.startswith(CRITICAL_PREFIX)]
    for warning in critical:
        lines.append("*** WARNING ***")
        lines.append(warning[len(CRITICAL_PREFIX):])
        lines.append("")
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
        if not warning.startswith(CRITICAL_PREFIX):
            lines.append(f"  Warning: {warning}")
    lines.append("")
    v = outcome.verify
    if v is not None:
        status = "clean" if v.clean else "NOT CLEAN"
        lines.append(f"Verification: {status}")
        if v.image_data_match and v.image_data_rebuilt:
            lines.append("  Image data rebuilt, pixels identical (SHA-256 of the "
                         f"decompressed image data {v.image_sha256})")
        elif v.image_data_match and "cleaned and verified" in v.image_label:
            # --deep: the files inside changed on purpose, each checked on its own
            lines.append(f"  {v.image_label}: the same files as the original "
                         f"(SHA-256 {v.image_sha256})")
        elif v.image_data_match:
            lines.append(f"  {v.image_label} unchanged (SHA-256 {v.image_sha256})")
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
