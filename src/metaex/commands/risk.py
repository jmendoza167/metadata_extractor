"""metaex risk: what a file's metadata reveals, grouped by risk level."""

from __future__ import annotations

import os
import sys
from typing import Any

from metaex.core.loader import DEFAULT_MAX_SIZE
from metaex.core.report import Report, sanitize
from metaex.core.risk import CRITICAL, HIGH, LEVELS, LOW, MEDIUM, RiskFinding, assess, counts

MAX_VALUE_CHARS = 80

# ANSI SGR codes per level.
COLORS = {CRITICAL: "\x1b[1;31m", HIGH: "\x1b[33m", MEDIUM: "\x1b[36m", LOW: "\x1b[2m"}
RESET = "\x1b[0m"


def assess_file(path: str, max_size: int = DEFAULT_MAX_SIZE) -> tuple[Report, list[RiskFinding], bool]:
    """(report from `show`, classified findings, whether metaex can strip this type)."""
    from metaex.commands.show import build_report
    from metaex.core import filetype as ft
    from metaex.core.loader import load_file
    from metaex.core.registry import can_strip, format_module

    report = build_report(path, max_size=max_size)
    detected = ft.detect(load_file(path, max_size), path)
    return report, assess(report.sections), can_strip(format_module(detected))


def use_color(no_color_flag: bool, stream: Any = None) -> bool:
    """Color only for a terminal, without --no-color and without NO_COLOR set."""
    stream = stream if stream is not None else sys.stdout
    if no_color_flag or "NO_COLOR" in os.environ:
        return False
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty) or not isatty():
        return False
    return sys.platform != "win32" or _enable_windows_ansi()


def _enable_windows_ansi() -> bool:
    """Turn on ANSI escape processing for the Windows console; False if impossible."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError, ValueError):
        return False


def summary_line(findings: list[RiskFinding]) -> str:
    c = counts(findings)
    return ", ".join(f"{c[level]} {level}" for level in LEVELS)


def risk_lines(path: str, report: Report, findings: list[RiskFinding], strippable: bool,
               color: bool = False) -> list[str]:
    """Text output; every value is sanitized here (color codes are added after)."""
    def paint(level: str, text: str) -> str:
        return f"{COLORS[level]}{text}{RESET}" if color else text

    lines = [f"metaex risk: {sanitize(path)}", summary_line(findings), ""]
    notes = [w for w in report.warnings if not w.startswith("CRITICAL")]
    if notes:
        lines.append("Notes:")
        lines += [f"  ! {sanitize(n)}" for n in notes]
        lines.append("")
    if not findings:
        lines.append("No embedded metadata found.")
        return lines
    width = min(40, max(len(sanitize(f"{f.section}: {f.label}")) for f in findings))
    for level in LEVELS:
        items = [f for f in findings if f.level == level]
        if not items:
            continue
        lines.append(paint(level, f"{level.upper()} ({len(items)})"))
        for f in items:
            name = sanitize(f"{f.section}: {f.label}")
            value = sanitize(f.value)
            if len(value) > MAX_VALUE_CHARS:
                value = value[: MAX_VALUE_CHARS - 3] + "..."
            lines.append(f"  {paint(level, name.ljust(width))}  {value}")
        lines.append("")
    if strippable:
        lines.append(f'Run "metaex strip {sanitize(path)}" to remove these.')
    else:
        lines.append("metaex can't strip this type of file yet.")
    return lines


def risk_to_dict(path: str, report: Report, findings: list[RiskFinding],
                 strippable: bool) -> dict[str, Any]:
    return {
        "path": path,
        "summary": counts(findings),
        "findings": [{"level": f.level, "section": f.section, "label": f.label,
                      "value": f.value} for f in findings],
        "warnings": report.warnings,
        "can_strip": strippable,
    }


__all__ = ["assess_file", "use_color", "risk_lines", "risk_to_dict", "summary_line",
           "CRITICAL", "HIGH", "MEDIUM", "LOW"]
