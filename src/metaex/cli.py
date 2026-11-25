"""Command-line interface for metaex."""

from __future__ import annotations

import argparse
import math
import sys
import traceback
from typing import TYPE_CHECKING, Sequence

from metaex import __version__
from metaex.core.errors import MetaexError
from metaex.core.loader import DEFAULT_MAX_SIZE, MIB
from metaex.core.report import render_json, render_text, sanitize, write_text

if TYPE_CHECKING:
    from metaex.commands.batch import BatchRun

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_CLEAN = 3


def _add_common_options(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--max-size",
        type=_size_in_mib,
        default=DEFAULT_MAX_SIZE,
        metavar="MB",
        help=f"refuse files larger than this many MiB (default {DEFAULT_MAX_SIZE // MIB})",
    )
    sub.add_argument("--debug", action="store_true", help="show tracebacks on errors")


def _add_paths(sub: argparse.ArgumentParser, what: str) -> None:
    sub.add_argument("paths", nargs="+", metavar="path",
                     help=f"{what}; folders are searched one level deep (see --recursive)")
    sub.add_argument("-r", "--recursive", action="store_true",
                     help="also search the subfolders of folders")
    sub.add_argument("--include-hidden", action="store_true",
                     help="also include hidden files and folders inside folders (never .git, "
                          "node_modules, virtual environments or symbolic links)")


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="metaex",
        description="View and remove metadata from files.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"metaex {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    show_help = "show the metadata found in a file"
    show = subparsers.add_parser("show", help=show_help, description=show_help)
    _add_paths(show, "files or folders to examine")
    show.add_argument("--json", action="store_true", help="print the report as JSON")
    show.add_argument(
        "--structure",
        action="store_true",
        help="also list the file's low-level structure (e.g. JPEG segments)",
    )
    show.add_argument(
        "--extract-thumbnail",
        metavar="OUT",
        help="save the embedded EXIF thumbnail to OUT (a new file; never overwrites)",
    )
    _add_common_options(show)
    show.set_defaults(handler=_cmd_show)

    strip_help = "write a verified copy of a file with its metadata removed"
    strip = subparsers.add_parser("strip", help=strip_help, description=strip_help)
    _add_paths(strip, "files or folders to clean (never modified unless --in-place)")
    target = strip.add_mutually_exclusive_group()
    target.add_argument("-o", "--output", metavar="OUTPUT",
                        help="one file only: where to write the cleaned copy (default: "
                             "<name>_clean.<ext> next to the original)")
    target.add_argument("--output-dir", metavar="DIR",
                        help="write the cleaned copies into DIR (created if missing), with "
                             "their original names and folder structure")
    target.add_argument("--in-place", action="store_true",
                        help="replace the original files (asks for confirmation)")
    target.add_argument("--rename", action="store_true",
                        help="write to a random name like img_8f3a2c1d.jpg in the same folder")
    strip.add_argument("--overwrite", action="store_true",
                       help="replace cleaned copies that already exist (never the originals; "
                            "without this option they are skipped)")
    strip.add_argument("--yes", action="store_true",
                       help="don't ask for confirmation with --in-place")
    strip.add_argument("--paranoid", action="store_true",
                       help="also remove the ICC color profile and other color and "
                            "density data (JPEG: JFIF density; PNG: gAMA, cHRM, cICP, "
                            "mDCv, cLLI, pHYs; GIF and WebP: the ICC profile only; SVG: "
                            "<title> and <desc>; office documents: in their images); "
                            "colors may shift. MP3: also the Xing/LAME information frame "
                            "(duration and seeking may be less accurate); WAV: also cue "
                            "points and sampler loops; FLAC: also the cue sheet")
    strip.add_argument("--no-orientation", action="store_true",
                       help="don't keep the EXIF Orientation tag (the image may display rotated)")
    strip.add_argument("--rebuild-image-data", action="store_true",
                       help="PNG: if hidden data is found inside the compressed image "
                            "data, compress the image data again without it (pixels stay "
                            "identical; without this option such files are refused)")
    strip.add_argument("--remove-comments", action="store_true",
                       help='Office documents: remove comments completely (by default they '
                            'are kept and every author becomes "Author")')
    strip.add_argument("--deep", action="store_true",
                       help="archives (ZIP, TAR, gzip, bzip2, xz): also clean the files "
                            "inside, each verified; files metaex can't clean are copied "
                            "unchanged and listed")
    strip.add_argument("--fixed-time", action="store_true",
                       help="set the output's modified/accessed time to 2000-01-01T00:00:00Z")
    strip.add_argument("--json", action="store_true", help="print the summary as JSON")
    _add_common_options(strip)
    strip.set_defaults(handler=_cmd_strip)

    verify_help = ("check that a cleaned file contains no metadata "
                   "(exit 0 = clean, 3 = not clean, 1 = error)")
    verify = subparsers.add_parser("verify", help=verify_help, description=verify_help)
    verify.add_argument("path", help="cleaned file to check")
    verify.add_argument("--original", metavar="PATH",
                        help="original file, to confirm the image data is unchanged")
    verify.add_argument("--json", action="store_true", help="print the result as JSON")
    _add_common_options(verify)
    verify.set_defaults(handler=_cmd_verify)

    risk_help = "summarize what a file's metadata reveals, by risk level"
    risk = subparsers.add_parser("risk", help=risk_help, description=risk_help)
    _add_paths(risk, "files or folders to examine")
    risk.add_argument("--json", action="store_true", help="print the findings as JSON")
    risk.add_argument("--no-color", action="store_true", help="never use colors")
    _add_common_options(risk)
    risk.set_defaults(handler=_cmd_risk)

    formats_help = "list the supported formats, what strip removes, and the refused formats"
    formats = subparsers.add_parser("formats", help=formats_help, description=formats_help)
    formats.add_argument("--json", action="store_true", help="print the list as JSON")
    formats.set_defaults(handler=_cmd_formats)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the metaex CLI and return a process exit code.

    argparse handles --help, --version and usage errors by raising
    SystemExit; that is caught here so main() always returns an int and
    only the top-level entry point exits the process. Errors raised by a
    command become one line on stderr (plus a traceback with --debug).
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return _exit_code(exc.code)

    if args.command is None:
        parser.print_help()
        return EXIT_OK

    debug = getattr(args, "debug", False)
    try:
        return args.handler(args)
    except MetaexError as exc:
        if debug:
            traceback.print_exc()
        _print_error(str(exc))
        return EXIT_ERROR
    except KeyboardInterrupt:
        _print_error("interrupted")
        return EXIT_ERROR
    except Exception as exc:  # a bug in metaex, not a problem with the file
        if debug:
            traceback.print_exc()
            _print_error(f"internal error: {type(exc).__name__}: {exc}")
        else:
            _print_error(f"internal error: {type(exc).__name__}: {exc} "
                         "(run with --debug for details)")
        return EXIT_ERROR


def _cmd_show(args: argparse.Namespace) -> int:
    from metaex.commands.show import build_report, extract_thumbnail
    from metaex.core.batch import is_batch

    if is_batch(args.paths):
        if args.extract_thumbnail:
            return _usage_error("show", "--extract-thumbnail works on one file only")
        return _batch_show(args)
    args.path = args.paths[0]
    report = build_report(args.path, max_size=args.max_size, structure=args.structure)
    saved = None
    if args.extract_thumbnail:
        saved = extract_thumbnail(args.path, args.extract_thumbnail, max_size=args.max_size)
    write_text(render_json(report) if args.json else render_text(report))
    if saved is not None:
        # stderr, so --json output on stdout stays valid JSON
        write_text(f"metaex: saved embedded thumbnail ({saved} bytes) to "
                   f"{sanitize(args.extract_thumbnail)}\n", sys.stderr)
    return EXIT_OK


def _cmd_strip(args: argparse.Namespace) -> int:
    import json

    from metaex.commands.strip import strip_file, strip_into_folder, summary_lines
    from metaex.core.batch import is_batch

    if is_batch(args.paths):
        return _batch_strip(args)
    args.path = args.paths[0]
    if args.in_place and not args.yes:
        _confirm_overwrite(args.path)
    options = dict(paranoid=args.paranoid, keep_orientation=not args.no_orientation,
                   max_size=args.max_size, fixed_time=args.fixed_time,
                   rebuild_image_data=args.rebuild_image_data,
                   remove_comments=args.remove_comments, deep=args.deep,
                   overwrite=args.overwrite)
    if args.output_dir is not None:
        if _not_a_folder(args.output_dir):
            return _usage_error("strip", f"--output-dir {args.output_dir}: exists and is not "
                                         "a folder")
        outcome = strip_into_folder(args.path, args.output_dir, **options)
    else:
        outcome = strip_file(args.path, args.output, in_place=args.in_place, rename=args.rename,
                             **options)
    if args.json:
        write_text(json.dumps(outcome.to_dict(), indent=2, ensure_ascii=True) + "\n")
    else:
        write_text("\n".join(sanitize(line) for line in summary_lines(outcome)) + "\n")
    return EXIT_OK


def _cmd_verify(args: argparse.Namespace) -> int:
    import json

    from metaex.commands.verify import result_lines, result_to_dict, verify_file

    result = verify_file(args.path, args.original, max_size=args.max_size)
    if args.json:
        write_text(json.dumps(result_to_dict(args.path, result), indent=2,
                              ensure_ascii=True) + "\n")
    else:
        write_text("\n".join(sanitize(line) for line in result_lines(args.path, result)) + "\n")
    return EXIT_OK if result.clean else EXIT_NOT_CLEAN


def _confirm_overwrite(path: str) -> None:
    """Ask on the terminal before replacing a file; refuse if there is no terminal."""
    stdin = sys.stdin
    if stdin is None or not stdin.isatty():
        raise MetaexError("--in-place needs confirmation, but input is not a terminal; "
                          "pass --yes to overwrite without asking. Nothing changed.")
    write_text(f"Type 'yes' to overwrite {sanitize(path)}: ", sys.stderr)
    answer = stdin.readline()
    if answer.strip() != "yes":
        raise MetaexError("not confirmed; nothing changed")


def _confirm_overwrite_many(count: int) -> None:
    """One confirmation for a whole --in-place batch."""
    stdin = sys.stdin
    if stdin is None or not stdin.isatty():
        raise MetaexError("--in-place needs confirmation, but input is not a terminal; "
                          "pass --yes to overwrite without asking. Nothing changed.")
    noun = "file" if count == 1 else "files"
    write_text(f"Type 'yes' to overwrite {count} {noun}: ", sys.stderr)
    answer = stdin.readline()
    if answer.strip() != "yes":
        raise MetaexError("not confirmed; nothing changed")


def _cmd_risk(args: argparse.Namespace) -> int:
    import json

    from metaex.commands.risk import assess_file, risk_lines, risk_to_dict, use_color
    from metaex.core.batch import is_batch

    if is_batch(args.paths):
        return _batch_risk(args)
    args.path = args.paths[0]
    report, findings, strippable = assess_file(args.path, max_size=args.max_size)
    if args.json:
        write_text(json.dumps(risk_to_dict(args.path, report, findings, strippable), indent=2,
                              ensure_ascii=True) + "\n")
    else:
        lines = risk_lines(args.path, report, findings, strippable,
                           color=use_color(args.no_color))
        write_text("\n".join(lines) + "\n")
    return EXIT_OK


def _cmd_formats(args: argparse.Namespace) -> int:
    import json

    from metaex.commands.formats import formats_lines, formats_to_dict

    if args.json:
        write_text(json.dumps(formats_to_dict(), indent=2, ensure_ascii=True) + "\n")
    else:
        write_text("\n".join(formats_lines()) + "\n")
    return EXIT_OK


# -- many files ------------------------------------------------------------------------


def _collect(args: argparse.Namespace, *, skip_clean_outputs: bool = True,
             exclude: Sequence[str] = ()):
    from metaex.core.batch import collect

    return collect(args.paths, recursive=args.recursive, include_hidden=args.include_hidden,
                   skip_clean_outputs=skip_clean_outputs, exclude=exclude)


def _run_batch(args: argparse.Namespace, collection, work, *, refuse_unsupported: bool,
               print_result) -> BatchRun:
    from metaex.commands import batch

    def process_item(item):
        return batch.process(item, lambda result: work(item, result), max_size=args.max_size,
                             refuse_unsupported=refuse_unsupported, debug=args.debug)

    def on_result(result, index, total):
        if not args.json:
            print_result(result)

    run = batch.run(collection, process_item, on_result)
    if run.interrupted:
        _print_error("interrupted")
    return run


def _report_printer(render):
    """show and risk: each file's report under a header with its path;
    failures under the header too; skipped files only in the summary."""
    from metaex.commands import batch

    def print_result(result):
        if result.outcome == batch.SKIPPED:
            return
        write_text(batch.file_header(sanitize(result.path)))
        if result.outcome in batch.FAILURES:
            write_text(sanitize(f"{result.outcome.capitalize()}: {batch.reason_text(result)}")
                       + "\n\n")
        else:
            write_text(render(result))

    return print_result


def _batch_json(command: str, run, file_data) -> None:
    import json

    from metaex.commands import batch

    files = []
    for result in batch.results_in_order(run.results):
        entry = {"path": result.path, "outcome": result.outcome, "format": result.format,
                 "reason": result.reason}
        entry.update(file_data(result) if result.payload is not None else {})
        files.append(entry)
    write_text(json.dumps({"command": command, "interrupted": run.interrupted,
                           "files": files}, indent=2, ensure_ascii=True) + "\n")


def _batch_show(args: argparse.Namespace) -> int:
    from metaex.commands import batch
    from metaex.commands.show import build_report
    from metaex.core.report import report_to_data

    def work(item, result):
        result.payload = build_report(item.path, max_size=args.max_size,
                                      structure=args.structure)
        return batch.EXAMINED

    run = _run_batch(args, _collect(args), work, refuse_unsupported=False,
                     print_result=_report_printer(lambda r: render_text(r.payload) + "\n"))
    if args.json:
        _batch_json("show", run, lambda r: {"report": report_to_data(r.payload)})
    return batch.exit_code(run)


def _batch_risk(args: argparse.Namespace) -> int:
    from metaex.commands import batch
    from metaex.commands.risk import assess_file, risk_lines, risk_to_dict, use_color

    color = use_color(args.no_color)

    def work(item, result):
        result.payload = assess_file(item.path, max_size=args.max_size)
        return batch.EXAMINED

    def render(result):
        report, findings, strippable = result.payload
        return "\n".join(risk_lines(result.path, report, findings, strippable,
                                    color=color)) + "\n\n"

    run = _run_batch(args, _collect(args), work, refuse_unsupported=False,
                     print_result=_report_printer(render))
    if args.json:
        _batch_json("risk", run, lambda r: {"risk": risk_to_dict(r.path, *r.payload)})
    return batch.exit_code(run)


def _batch_strip(args: argparse.Namespace) -> int:
    import os

    from metaex.commands import batch
    from metaex.commands.strip import BatchStripper
    from metaex.core.batch import native, path_key, same_path

    if args.output is not None:
        return _usage_error("strip", "-o/--output names one output file; with folders or "
                                     "several files use --output-dir DIR")
    out = native(args.output_dir) if args.output_dir is not None else None
    if out is not None:
        if _not_a_folder(out):
            return _usage_error("strip", f"--output-dir {out}: exists and is not a folder")
        for path in args.paths:
            if os.path.isdir(path) and same_path(path, out):
                return _usage_error("strip", f"--output-dir {out} is an input folder; choose "
                                             "another folder (it may be inside the input "
                                             "folder)")
    collection = _collect(args, exclude=[out] if out is not None else [])
    if args.in_place and not args.yes and collection.items:
        _confirm_overwrite_many(len(collection.items))
    stripper = BatchStripper(
        inputs={path_key(item.path) for item in collection.items}, output_dir=out,
        in_place=args.in_place, rename=args.rename, overwrite=args.overwrite,
        paranoid=args.paranoid, keep_orientation=not args.no_orientation,
        max_size=args.max_size, fixed_time=args.fixed_time,
        rebuild_image_data=args.rebuild_image_data, remove_comments=args.remove_comments,
        deep=args.deep)

    def work(item, result):
        outcome, value = stripper.strip(item.path, item.relative, result.filetype)
        if outcome == batch.SKIPPED:
            result.reason = value
        else:
            result.payload, result.output = value, value.output
        return outcome

    run = _run_batch(args, collection, work, refuse_unsupported=True,
                     print_result=_print_strip_line)
    if args.json:
        _batch_json("strip", run, lambda r: {"output": r.output, "strip": r.payload.to_dict()})
    return batch.exit_code(run)


def _not_a_folder(path: str) -> bool:
    import os

    return os.path.lexists(path) and not os.path.isdir(path)


def _print_strip_line(result) -> None:
    """One line per file: outcome, path and output (or the reason), then the
    file's warnings and, if not CLEAN, the verification verdict."""
    from metaex.commands import batch
    from metaex.core.report import CRITICAL_PREFIX
    from metaex.core.verify import verdict

    line = f"{result.outcome}: {result.path}"
    if result.output is not None:
        line += f" -> {result.output}"
    if result.reason:
        line += f": {batch.reason_text(result)}"
    lines = [line]
    outcome = result.payload
    if outcome is not None:
        for warning in outcome.warnings:
            if warning.startswith(CRITICAL_PREFIX):
                warning = "*** " + warning[len(CRITICAL_PREFIX):]
            lines.append(f"  Warning: {warning}")
        if outcome.verify is not None and not outcome.verify.clean:
            lines.append(f"  Verification: {verdict(outcome.verify)}")
    write_text("\n".join(sanitize(line) for line in lines) + "\n")


def _usage_error(command: str, message: str) -> int:
    write_text(f"metaex {command}: error: {sanitize(message)}\n", sys.stderr)
    return EXIT_USAGE


def _size_in_mib(text: str) -> int:
    """argparse type for --max-size: a positive number of MiB, as bytes."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive number of MiB, not {text!r}")
    return max(1, int(value * MIB))


def _print_error(message: str) -> None:
    write_text(f"metaex: error: {sanitize(message)}\n", sys.stderr)


def _exit_code(code: object) -> int:
    if code is None:
        return EXIT_OK
    if isinstance(code, int):
        return code
    return EXIT_ERROR
