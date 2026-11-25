"""Run a command on many files: every file gets an outcome, and one bad file
never stops the others.

Each file goes through exactly the same single-file code as when it is named
alone (build_report, assess_file, strip_file, verify_file); this module only
decides which files to give it, catches what goes wrong with each one and
records the outcome.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from metaex.core import filetype as ft
from metaex.core.batch import Collection, Item, display_path, sort_key
from metaex.core.errors import FileAccessError, MetaexError
from metaex.core.loader import load_file
from metaex.core.registry import can_strip, format_module

# Outcomes. strip: cleaned / already clean; verify: clean / not clean;
# show and risk: examined. Every command: skipped, refused, error.
CLEANED = "cleaned"
ALREADY_CLEAN = "already clean"
CLEAN = "clean"
NOT_CLEAN = "not clean"
EXAMINED = "examined"
SKIPPED = "skipped"
REFUSED = "refused"
ERROR = "error"
OUTCOMES = (EXAMINED, CLEANED, ALREADY_CLEAN, CLEAN, NOT_CLEAN, SKIPPED, REFUSED, ERROR)
FAILURES = (REFUSED, ERROR)


@dataclass
class FileResult:
    path: str
    outcome: str = ""
    format: str = ""
    """Detected type ("unknown" if not recognized; "" if the file was not read)."""
    reason: str = ""
    """Why the file was skipped, refused or failed."""
    relative: str = ""
    """Path inside the folder argument ("/"-separated), or the file name."""
    output: str | None = None
    """strip: where the cleaned copy was written."""
    payload: Any = None
    """The command's own result for this file (Report, StripOutcome, ...)."""
    filetype: ft.FileType | None = field(default=None, repr=False)
    """The detected type, for the command's work function."""


@dataclass
class BatchRun:
    results: list[FileResult] = field(default_factory=list)
    interrupted: bool = False
    total: int = 0
    """Number of files to process (not counting what was skipped or missing
    before processing started)."""
    processed: int = 0
    """Number of those files processed (less than total if interrupted)."""


def unsupported_reason(detected: ft.FileType) -> str:
    kind = "unknown" if detected.key == ft.UNKNOWN.key else detected.name
    return f"unsupported format (detected type: {kind})"


def process(item: Item, work: Callable[[FileResult], str], *, max_size: int,
            refuse_unsupported: bool, debug: bool = False) -> FileResult:
    """Detect the file's type, skip unsupported formats, run ``work`` (which
    returns the outcome) and turn every error into the file's outcome.

    Unsupported formats are skipped, except files named on the command line
    when ``refuse_unsupported`` (strip, verify): ``work`` runs and refuses
    them with the same message as for a single file.
    KeyboardInterrupt is never caught here.
    """
    result = FileResult(display_path(item.path), relative=item.relative_text)
    try:
        data = load_file(item.path, max_size)
        detected = ft.detect(data, item.path)
        del data
        result.format = detected.name
        result.filetype = detected
        if not can_strip(format_module(detected)) and not (item.explicit and refuse_unsupported):
            result.outcome, result.reason = SKIPPED, unsupported_reason(detected)
            return result
        result.outcome = work(result)
    except FileAccessError as exc:
        result.outcome, result.reason = ERROR, str(exc)
    except MetaexError as exc:
        result.outcome, result.reason = REFUSED, str(exc)
    except OSError as exc:
        result.outcome, result.reason = ERROR, f"{result.path}: {exc.strerror or exc}"
    except Exception as exc:  # a bug in metaex: report it for this file and go on
        if debug:
            traceback.print_exc()
        result.outcome = ERROR
        result.reason = f"internal error: {type(exc).__name__}: {exc}"
    return result


def run(collection: Collection, process_item: Callable[[Item], FileResult],
        on_result: Callable[[FileResult, int, int], None] | None = None) -> BatchRun:
    """Process every collected file in order. Paths that could not be read
    and entries skipped while collecting become results too. Ctrl+C stops
    after the current file: the results so far are returned, marked
    interrupted (the single-file code never leaves temp files behind)."""
    batch = BatchRun(total=len(collection.items))
    for missing in collection.errors:
        result = FileResult(display_path(missing.path), ERROR, reason=missing.reason)
        batch.results.append(result)
        if on_result is not None:
            on_result(result, 0, batch.total)
    for skipped in collection.skipped:
        batch.results.append(FileResult(display_path(skipped.path), SKIPPED,
                                        reason=skipped.reason))
    try:
        for index, item in enumerate(collection.items, 1):
            result = process_item(item)
            batch.results.append(result)
            batch.processed += 1
            if on_result is not None:
                on_result(result, index, batch.total)
    except KeyboardInterrupt:
        batch.interrupted = True
    return batch


def exit_code(batch: BatchRun, *, not_clean_code: int | None = None) -> int:
    """1 if a file was refused or failed (or the run was interrupted), else
    ``not_clean_code`` if a file was not clean (verify), else 0."""
    outcomes = {r.outcome for r in batch.results}
    if batch.interrupted or outcomes & set(FAILURES):
        return 1
    if not_clean_code is not None and NOT_CLEAN in outcomes:
        return not_clean_code
    return 0


def results_in_order(results: Iterable[FileResult]) -> list[FileResult]:
    """Results sorted by path (the order files are processed in)."""
    return sorted(results, key=lambda r: sort_key(r.path))


def reason_text(result: FileResult) -> str:
    """The reason without the file's path in front (error messages usually
    start with it, and it is shown next to the reason anyway)."""
    reason = result.reason
    prefix = result.path + ": "
    return reason[len(prefix):] if reason.startswith(prefix) else reason


def file_header(path: str) -> str:
    rule = "=" * 72
    return f"{rule}\n==> {path}\n{rule}\n"


# -- summary ---------------------------------------------------------------------------

COMMAND_OUTCOMES = {
    "show": (EXAMINED, SKIPPED, REFUSED, ERROR),
    "risk": (EXAMINED, SKIPPED, REFUSED, ERROR),
    "strip": (CLEANED, ALREADY_CLEAN, SKIPPED, REFUSED, ERROR),
    "verify": (CLEAN, NOT_CLEAN, SKIPPED, REFUSED, ERROR),
}
NOT_READ = "(not read)"
"""Format name for entries whose type was never detected (skipped while
collecting, missing, unreadable)."""
LISTED = (REFUSED, ERROR, SKIPPED)
"""Outcomes listed file by file in the summary, with the reason."""


def summarize(command: str, batch: BatchRun) -> dict[str, Any]:
    """Counts per outcome and per format, for the summary and --json."""
    outcomes = {o: 0 for o in COMMAND_OUTCOMES[command]}
    formats: dict[str, dict[str, int]] = {}
    for result in batch.results:
        outcomes[result.outcome] = outcomes.get(result.outcome, 0) + 1
        per_format = formats.setdefault(result.format or NOT_READ, {})
        per_format[result.outcome] = per_format.get(result.outcome, 0) + 1
    summary: dict[str, Any] = {
        "files": len(batch.results),
        "outcomes": outcomes,
        "formats": {name: {o: formats[name][o] for o in OUTCOMES if o in formats[name]}
                    for name in sorted(formats, key=_format_order)},
        "interrupted": batch.interrupted,
        "to_process": batch.total,
        "processed": batch.processed,
    }
    if command == "risk":
        summary["risk"] = _risk_totals(batch)
    return summary


def _risk_totals(batch: BatchRun) -> dict[str, dict[str, int]]:
    """risk: per level, the findings in all examined files and how many
    files have at least one."""
    from metaex.core.risk import LEVELS, counts

    totals = {level: {"findings": 0, "files": 0} for level in LEVELS}
    for result in batch.results:
        if result.payload is None:
            continue
        for level, n in counts(result.payload[1]).items():
            totals[level]["findings"] += n
            totals[level]["files"] += 1 if n else 0
    return totals


def _critical_files(batch: BatchRun) -> list[tuple[str, int]]:
    from metaex.core.risk import CRITICAL, counts

    found = []
    for result in results_in_order(batch.results):
        if result.payload is not None:
            n = counts(result.payload[1])[CRITICAL]
            if n:
                found.append((result.path, n))
    return found


def _format_order(name: str) -> tuple[bool, str]:
    return (name == NOT_READ, name.casefold())


def summary_lines(command: str, batch: BatchRun) -> list[str]:
    """The text summary printed after a batch run (unsanitized)."""
    summary = summarize(command, batch)
    noun = "entry" if summary["files"] == 1 else "entries"
    lines = ["", f"Summary: {summary['files']} {noun}"]
    if batch.interrupted:
        lines.append(f"INTERRUPTED: stopped after {batch.processed} of {batch.total} file(s); "
                     "this summary is partial")
    width = max(len(o) for o in summary["outcomes"])
    for outcome, count in summary["outcomes"].items():
        lines.append(f"  {outcome.ljust(width)}  {count}")
    if "risk" in summary:
        lines += ["", "Findings in the examined files:"]
        for level, total in summary["risk"].items():
            files = "file" if total["files"] == 1 else "files"
            lines.append(f"  {level.ljust(8)}  {total['findings']} (in {total['files']} {files})")
        critical = _critical_files(batch)
        if critical:
            lines += ["", f"Files with critical findings ({len(critical)}):"]
            lines += [f"  {path}: {n} critical" for path, n in critical]
    if summary["formats"]:
        lines += ["", "By format:"]
        width = max(len(name) for name in summary["formats"])
        for name, counts in summary["formats"].items():
            total = sum(counts.values())
            detail = ", ".join(f"{counts[o]} {o}" for o in OUTCOMES if o in counts)
            lines.append(f"  {name.ljust(width)}  {total} ({detail})")
    for outcome in LISTED:
        listed = [r for r in results_in_order(batch.results) if r.outcome == outcome]
        if listed:
            lines += ["", f"{outcome.capitalize()} ({len(listed)}):"]
            lines += [f"  {r.path}: {reason_text(r)}" for r in listed]
    if any(r.reason.startswith("unsupported format") for r in batch.results):
        lines += ["", 'Run "metaex formats" to see the supported formats.']
    return lines
