"""metaex show: report everything metaex can currently tell about a file."""

from __future__ import annotations

import importlib.util
import os

from metaex.core import filetype as ft
from metaex.core.generic import file_system_section, hashes_section, mark_of_the_web_section
from metaex.core.loader import DEFAULT_MAX_SIZE, load_file
from metaex.core.report import Report
from metaex.core.result import Section


def build_report(path: str | os.PathLike[str], max_size: int = DEFAULT_MAX_SIZE) -> Report:
    """load -> detect type -> file system metadata + hashes -> Report."""
    name = os.fspath(path)
    data = load_file(name, max_size)
    detected = ft.detect(data, name)

    report = Report(subject=name)
    report.warnings.extend(detected.warnings)
    has_parser = has_format_handler(detected)
    if not has_parser:
        report.warnings.append(_not_examined_warning(detected))

    report.sections.append(_type_section(detected, has_parser))
    report.sections.append(file_system_section(name))
    motw = mark_of_the_web_section(name)
    if motw is not None:
        report.sections.append(motw)
    report.sections.append(hashes_section(name))
    return report


def has_format_handler(filetype: ft.FileType) -> bool:
    """True if metaex.formats has a module for this (supported) type."""
    if not filetype.supported or not filetype.key.isidentifier():
        return False
    return importlib.util.find_spec(f"metaex.formats.{filetype.key}") is not None


def support_status(filetype: ft.FileType, has_parser: bool) -> str:
    if filetype.key == ft.UNKNOWN.key:
        return "not supported (unrecognized format)"
    if not filetype.supported:
        return "not supported"
    if has_parser:
        return "supported"
    return "planned (not implemented yet)"


def _type_section(detected: ft.FileType, has_parser: bool) -> Section:
    section = Section("file_type", "File type")
    section.add("name", "Detected type", detected.name)
    section.add("mime", "MIME type", detected.mime)
    if detected.extensions:
        section.add("extensions", "Usual extensions", list(detected.extensions))
    section.add("support", "metaex support", support_status(detected, has_parser))
    section.add("embedded_metadata", "Embedded metadata",
                "examined" if has_parser else "not examined")
    return section


def _not_examined_warning(detected: ft.FileType) -> str:
    if detected.key == ft.UNKNOWN.key:
        what = "file type not recognized"
    elif not detected.supported:
        what = f"metaex does not support {detected.name} files"
    else:
        what = f"metaex has no {detected.name} parser yet"
    return (f"{what}; embedded metadata (EXIF, document properties, tags, ...) "
            "was NOT examined. Only file system information is shown.")
