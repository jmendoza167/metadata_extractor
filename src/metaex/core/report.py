"""Render a report (warnings plus sections of fields) for people to read.

Values come from untrusted files, so the text renderer escapes control and
invisible formatting characters: a crafted metadata value must not be able
to move the cursor, recolor the terminal or reverse text direction.
"""

from __future__ import annotations

import shutil
import sys
import textwrap
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, TextIO

from metaex.core.result import Section

DEFAULT_WIDTH = 100
MIN_WIDTH = 60
MAX_WIDTH = 160
MAX_LABEL_WIDTH = 24
MAX_VALUE_LINES = 6
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
            label = sanitize(f.label)
            if len(label) > label_width:
                label = label[: label_width - 3] + "..."
            text = format_value(f.value)
            if f.note:
                text += f" ({sanitize(f.note)})"
            lines = _wrap_value(text, value_width)
            out.append(f"  {mark} {label.ljust(label_width)}  {lines[0]}".rstrip())
            out += [" " * value_col + line for line in lines[1:]]
        out.append("")

    if any_sensitive:
        out.append(f"{SENSITIVE_MARK} = sensitive: can reveal who made the file, "
                   "where, or where it came from")
        out.append("")

    return "\n".join(out).rstrip("\n") + "\n"


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
    sequence instead of raising UnicodeEncodeError.
    """
    stream = stream if stream is not None else sys.stdout
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        stream.write(text.encode(encoding, "backslashreplace").decode(encoding))
    stream.flush()


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
