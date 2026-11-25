"""CSV output: one row per finding, ready for a spreadsheet.

Every cell comes from an untrusted file (its metadata, but also its name),
so a cell that a spreadsheet would run as a formula (starting with =, +, -,
@, a tab or a carriage return) gets a leading apostrophe: Excel, LibreOffice
and Google Sheets then show it as text. The file is UTF-8 with a byte order
mark (so Excel detects the encoding) and CRLF line ends; quotes, commas and
line breaks inside cells are quoted by the csv module.
"""

from __future__ import annotations

import csv
import io
import sys
from collections.abc import Iterable
from typing import Any, BinaryIO

from metaex.core.report import Report, format_value
from metaex.core.risk import NOT_METADATA_SECTIONS, RiskFinding, classify

HEADER = ("path", "format", "section", "field", "value", "risk level")
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
BOM = "﻿"


def protect(cell: Any) -> str:
    """The cell as text, with a leading ' if a spreadsheet would treat it as a formula."""
    text = "" if cell is None else str(cell)
    return "'" + text if text.startswith(FORMULA_STARTS) else text


def report_rows(path: str, report: Report) -> list[tuple[str, ...]]:
    """show: every field of every section; metadata fields with their risk level."""
    kind = _report_format(report)
    rows = []
    for section in report.sections:
        for f in section.fields:
            value = format_value(f.value)
            level = ("" if section.key in NOT_METADATA_SECTIONS
                     else classify(section.key, f.key, f.label, value))
            rows.append((path, kind, section.title, f.label, value, level))
    return rows


def risk_rows(path: str, report: Report, findings: Iterable[RiskFinding]) -> list[tuple[str, ...]]:
    """risk: every classified finding."""
    kind = _report_format(report)
    return [(path, kind, f.section, f.label, f.value, f.level) for f in findings]


def verify_rows(path: str, result: Any,
                levels: dict[tuple[str, str, str], str] | None = None) -> list[tuple[str, ...]]:
    """verify: every leftover (with its risk level, looked up in ``levels`` by
    (section, field, value)) and every problem."""
    levels = levels or {}
    kind = result.filetype.name
    rows = [(path, kind, f.section, f.label, f.value,
             levels.get((f.section, f.label, f.value), "")) for f in result.leftovers]
    rows += [(path, kind, "", "Problem", problem, "") for problem in result.problems]
    return rows


def risk_levels(findings: Iterable[RiskFinding]) -> dict[tuple[str, str, str], str]:
    return {(f.section, f.label, f.value): f.level for f in findings}


def render(rows: Iterable[Iterable[Any]], header: bool = True) -> str:
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    if header:
        writer.writerow(HEADER)
    for row in rows:
        writer.writerow([protect(cell) for cell in row])
    return out.getvalue()


class CsvWriter:
    """Writes CSV text as UTF-8 bytes (the BOM first), bypassing the
    console's encoding and newline translation."""

    def __init__(self, stream: Any = None) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.started = False

    def write(self, rows: Iterable[Iterable[Any]]) -> None:
        text = render(rows, header=not self.started)
        if not self.started:
            text = BOM + text
            self.started = True
        buffer: BinaryIO | None = getattr(self.stream, "buffer", None)
        if buffer is not None:
            self.stream.flush()
            buffer.write(text.encode("utf-8"))
            buffer.flush()
        else:
            self.stream.write(text)
            self.stream.flush()


def _report_format(report: Report) -> str:
    for section in report.sections:
        if section.key == "file_type":
            field = section.get("name")
            if field is not None:
                return str(field.value)
    return ""
