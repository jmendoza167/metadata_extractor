"""metaex show: report everything metaex can currently tell about a file."""

from __future__ import annotations

import os
from types import ModuleType

from metaex.core import filetype as ft
from metaex.core.errors import MalformedFileError, MetaexError
from metaex.core.generic import file_system_section, hashes_section, mark_of_the_web_section
from metaex.core.loader import DEFAULT_MAX_SIZE, load_file
from metaex.core.registry import can_strip, format_module
from metaex.core.report import Report, unique_warnings
from metaex.core.result import Section
from metaex.core.writer import write_new_file


def extract_thumbnail(path: str | os.PathLike[str], out_path: str | os.PathLike[str],
                      max_size: int = DEFAULT_MAX_SIZE) -> int:
    """Save the embedded EXIF thumbnail of a JPEG to a new file; return its size.

    Never overwrites: fails if ``out_path`` already exists.
    """
    name = os.fspath(path)
    data = load_file(name, max_size)
    detected = ft.detect(data, name)
    if detected.key != ft.JPEG.key:
        raise MetaexError(f"{name}: thumbnail extraction is only supported for JPEG files "
                          f"(this file is {detected.name})")
    from metaex.formats import jpeg

    thumb = jpeg.exif_thumbnail(data)
    if thumb is None:
        raise MetaexError(f"{name}: no embedded EXIF thumbnail found")
    write_new_file(out_path, thumb)
    return len(thumb)


def build_report(path: str | os.PathLike[str], max_size: int = DEFAULT_MAX_SIZE,
                 structure: bool = False) -> Report:
    """load -> detect type -> format parser (if any) -> file system + hashes -> Report.

    ``structure`` adds the format's low-level layout (e.g. the JPEG segment list).
    """
    name = os.fspath(path)
    data = load_file(name, max_size)
    detected = ft.detect(data, name)

    report = Report(subject=name)
    report.warnings.extend(detected.warnings)
    module = format_module(detected)

    format_sections: list[Section] = []
    examined = "not examined"
    if module is None:
        report.warnings.append(_not_examined_warning(detected))
    else:
        try:
            parsed = module.parse(data)
        except MalformedFileError as exc:
            report.warnings.append(f"{detected.name} structure could not be read: {exc}; "
                                   "embedded metadata was NOT examined")
            examined = "not examined (file is malformed)"
        else:
            report.warnings.extend(parsed.get("warnings", []))
            format_sections = list(parsed.get("sections", []))
            if structure and parsed.get("structure") is not None:
                format_sections.append(parsed["structure"])
            examined = ("examined" if parsed.get("complete", True)
                        else "partly examined (file is malformed)")

    report.sections.append(_type_section(detected, module, examined))
    report.sections.extend(format_sections)
    report.sections.append(file_system_section(name))
    motw = mark_of_the_web_section(name)
    if motw is not None:
        report.sections.append(motw)
    report.sections.append(hashes_section(name))
    # Each warning once; most important first (the sort is stable, so the
    # order is otherwise kept).
    report.warnings = sorted(unique_warnings(report.warnings),
                             key=lambda w: not w.startswith("CRITICAL"))
    return report


def support_status(filetype: ft.FileType, module: ModuleType | None) -> str:
    if filetype.key == ft.UNKNOWN.key:
        return "not supported (unrecognized format)"
    if not filetype.supported:
        return "not supported"
    if module is None:
        return "planned (not implemented yet)"
    if can_strip(module):
        return "supported"
    return "viewing supported; stripping not implemented yet"


def _type_section(detected: ft.FileType, module: ModuleType | None, examined: str) -> Section:
    section = Section("file_type", "File type")
    section.add("name", "Detected type", detected.name)
    section.add("mime", "MIME type", detected.mime)
    if detected.extensions:
        section.add("extensions", "Usual extensions", list(detected.extensions))
    section.add("support", "metaex support", support_status(detected, module))
    section.add("embedded_metadata", "Embedded metadata", examined)
    return section


def _not_examined_warning(detected: ft.FileType) -> str:
    reason = ft.refusal(detected)
    if reason is not None:
        return (f"{reason}. Embedded metadata (document properties, authors, ...) was NOT "
                "examined. Only file system information is shown.")
    if detected.key == ft.UNKNOWN.key:
        what = "file type not recognized"
    elif not detected.supported:
        what = f"metaex does not support {detected.name} files"
    else:
        what = f"metaex has no {detected.name} parser yet"
    return (f"{what}; embedded metadata (EXIF, document properties, tags, ...) "
            "was NOT examined. Only file system information is shown.")
