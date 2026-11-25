"""Render a report (warnings plus sections of fields) for people to read.

Values come from untrusted files, so the text renderer escapes control and
invisible formatting characters: a crafted metadata value must not be able
to move the cursor, recolor the terminal or reverse text direction.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import textwrap
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, TextIO

from metaex import __version__
from metaex.core.result import Section

DEFAULT_WIDTH = 100
MIN_WIDTH = 60
MAX_WIDTH = 160
MAX_LABEL_WIDTH = 32
MAX_VALUE_LINES = 6
JSON_HEX_PREVIEW_BYTES = 32
JSON_MAX_DEPTH = 32
SENSITIVE_MARK = "*"


@dataclass
class Report:
    subject: str
    """What the report is about, usually the file path."""
    sections: list[Section] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def render_text(report: Report, width: int | None = None) -> str:
    """Render the report as aligned plain text, ending with a newline."""
    width = _clamp_width(width)
    labels = [f.label for s in report.sections for f in s.fields]
    label_width = min(MAX_LABEL_WIDTH, max((len(sanitize(l)) for l in labels), default=0))
    value_col = 2 + 2 + label_width + 2  # indent, sensitive mark, label, gap
    value_width = max(20, width - value_col)

    out: list[str] = [f"metaex report: {sanitize(report.subject)}", ""]

    if report.warnings:
        out += _header("Warnings")
        for warning in report.warnings:
            lines = textwrap.wrap(sanitize(warning), width=max(20, width - 4),
                                  break_long_words=True, break_on_hyphens=False) or [""]
            out.append("  ! " + lines[0])
            out += ["    " + line for line in lines[1:]]
        out.append("")

    any_sensitive = False
    for section in report.sections:
        out += _header(section.title)
        if not section.fields:
            out += ["  (nothing found)", ""]
            continue
        for f in section.fields:
            any_sensitive = any_sensitive or f.sensitive
            mark = SENSITIVE_MARK if f.sensitive else " "
            label_lines = wrap_label(sanitize(f.label), label_width)
            text = format_value(f.value)
            if f.note:
                text += f" ({sanitize(f.note)})"
            value_lines = _wrap_value(text, value_width)
            for i in range(max(len(label_lines), len(value_lines))):
                label = label_lines[i] if i < len(label_lines) else ""
                value = value_lines[i] if i < len(value_lines) else ""
                prefix = mark if i == 0 else " "
                out.append(f"  {prefix} {label.ljust(label_width)}  {value}".rstrip())
        out.append("")

    if any_sensitive:
        out.append(f"{SENSITIVE_MARK} = sensitive: can reveal who made the file, "
                   "where, or where it came from")
        out.append("")

    return "\n".join(out).rstrip("\n") + "\n"


CRITICAL_PREFIX = "CRITICAL: "


def unique_warnings(warnings: list[str]) -> list[str]:
    """Drop repeated warnings, keeping the first position and highest severity.

    Two warnings are the same if they match once a leading "CRITICAL: " is
    ignored; if either copy is critical, the critical one is kept.
    """
    out: list[str] = []
    index: dict[str, int] = {}
    for warning in warnings:
        key = warning[len(CRITICAL_PREFIX):] if warning.startswith(CRITICAL_PREFIX) else warning
        if key not in index:
            index[key] = len(out)
            out.append(warning)
        elif warning.startswith(CRITICAL_PREFIX):
            out[index[key]] = warning
    return out


def format_value(value: Any) -> str:
    """One-line, display-safe text for a field value."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        n = value.nbytes if isinstance(value, memoryview) else len(value)
        return f"<{n} bytes>"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return "(none)"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return ", ".join(format_value(v) for v in value) if value else "(none)"
    return sanitize(str(value))


def sanitize(text: str) -> str:
    """Escape characters that could alter terminal output or hide text.

    Control characters (C0/C1, including ESC), invisible formatting
    characters (bidi overrides, zero-width marks), line/paragraph separators
    and lone surrogates are replaced with visible escapes like \\x1b or
    \\u202e.
    """
    out = []
    for ch in text:
        if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp", "Cs"):
            out.append(_escape(ch))
        else:
            out.append(ch)
    return "".join(out)


def write_text(text: str, stream: TextIO | None = None) -> None:
    """Write text without crashing on consoles that can't encode it.

    If the stream's encoding (e.g. cp1252 when output is redirected on
    Windows) can't represent a character, it is written as an escape
    sequence instead of raising UnicodeEncodeError. Raises OutputClosedError
    if the reader has closed the stream (a pipe into ``head``).
    """
    from metaex.core.errors import OutputClosedError, is_closed_output

    stream = stream if stream is not None else sys.stdout
    try:
        try:
            stream.write(text)
        except UnicodeEncodeError:
            encoding = getattr(stream, "encoding", None) or "ascii"
            stream.write(text.encode(encoding, "backslashreplace").decode(encoding))
        stream.flush()
    except OSError as exc:
        if is_closed_output(exc):
            raise OutputClosedError from None
        raise


def _escape(ch: str) -> str:
    code = ord(ch)
    named = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    if ch in named:
        return named[ch]
    if code <= 0xFF:
        return f"\\x{code:02x}"
    if code <= 0xFFFF:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def wrap_label(label: str, width: int) -> list[str]:
    """Split a label into lines of at most ``width`` characters, never dropping text.

    Breaks are preferred after "/", "]" or a space (XMP paths such as
    xmpMM:History[1]/stEvt:action), and pieces longer than a line are cut.
    """
    if len(label) <= width:
        return [label]
    pieces: list[str] = []
    current = ""
    for ch in label:
        current += ch
        if ch in "/] ":
            pieces.append(current)
            current = ""
    if current:
        pieces.append(current)
    lines: list[str] = []
    line = ""
    for piece in pieces:
        while len(piece) > width:  # a single piece too long for any line
            if line:
                lines.append(line.rstrip())
                line = ""
            lines.append(piece[:width])
            piece = piece[width:]
        if len(line) + len(piece) > width:
            lines.append(line.rstrip())
            line = ""
        line += piece
    if line:
        lines.append(line.rstrip())
    return [l for l in lines if l] or [""]


def _wrap_value(text: str, width: int) -> list[str]:
    lines = textwrap.wrap(text, width=width, break_long_words=True,
                          break_on_hyphens=False, drop_whitespace=True) or [""]
    if len(lines) > MAX_VALUE_LINES:
        lines = lines[:MAX_VALUE_LINES]
        lines.append(f"[shortened: {len(text)} characters in total; "
                     "use --json to see the full value]")
    return lines


def _header(title: str) -> list[str]:
    title = sanitize(title)
    return [title, "-" * len(title)]


def _clamp_width(width: int | None) -> int:
    if width is None:
        width = shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns
    return max(MIN_WIDTH, min(MAX_WIDTH, width))


# -- JSON ------------------------------------------------------------------------


def render_json(report: Report) -> str:
    """Render the report as JSON text, ending with a newline.

    Key order is deterministic (report order, not alphabetical), datetimes
    become ISO 8601 strings and binary values become
    {"length": N, "hex_preview": "<first 32 bytes as hex>"}. Output is
    pure ASCII, so it can't be mangled by a console's encoding.
    """
    return json.dumps(report_to_data(report), indent=2, ensure_ascii=True,
                      allow_nan=False) + "\n"


def report_to_data(report: Report) -> dict[str, Any]:
    sections: dict[str, Any] = {}
    for section in report.sections:
        fields: dict[str, Any] = {}
        for f in section.fields:
            fields[_unique_key(fields, f.key)] = {
                "label": f.label,
                "value": to_jsonable(f.value),
                "sensitive": f.sensitive,
                "note": f.note,
            }
        sections[_unique_key(sections, section.key)] = {
            "title": section.title,
            "fields": fields,
        }
    return {
        "metaex_version": __version__,
        "subject": report.subject,
        "warnings": list(report.warnings),
        "sections": sections,
    }


def to_jsonable(value: Any, _depth: int = 0) -> Any:
    """Convert a field value into plain JSON types."""
    if _depth > JSON_MAX_DEPTH:
        return "<nested too deeply>"
    if isinstance(value, (bytes, bytearray, memoryview)):
        view = memoryview(value).cast("B")
        return {
            "length": len(view),
            "hex_preview": view[:JSON_HEX_PREVIEW_BYTES].hex(),
        }
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v, _depth + 1) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, _depth + 1) for k, v in value.items()}
    return str(value)


def _unique_key(existing: dict[str, Any], key: str) -> str:
    """Never drop data because two fields share a key: add _2, _3, ..."""
    if key not in existing:
        return key
    n = 2
    while f"{key}_{n}" in existing:
        n += 1
    return f"{key}_{n}"
