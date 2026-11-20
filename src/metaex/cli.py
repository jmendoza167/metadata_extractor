"""Command-line interface for metaex."""

from __future__ import annotations

import argparse
import math
import sys
import traceback
from typing import Sequence

from metaex import __version__
from metaex.core.errors import MetaexError
from metaex.core.loader import DEFAULT_MAX_SIZE, MIB
from metaex.core.report import render_json, render_text, sanitize, write_text

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
    show.add_argument("path", help="file to examine")
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
    strip.add_argument("path", help="file to clean (never modified unless --in-place)")
    target = strip.add_mutually_exclusive_group()
    target.add_argument("-o", "--output", metavar="OUTPUT",
                        help="where to write the cleaned copy (default: <name>_clean.<ext>)")
    target.add_argument("--in-place", action="store_true",
                        help="replace the original file (asks for confirmation)")
    target.add_argument("--rename", action="store_true",
                        help="write to a random name like img_8f3a2c1d.jpg in the same folder")
    strip.add_argument("--yes", action="store_true",
                       help="don't ask for confirmation with --in-place")
    strip.add_argument("--paranoid", action="store_true",
                       help="also remove the ICC color profile and JFIF density "
                            "(colors may shift)")
    strip.add_argument("--no-orientation", action="store_true",
                       help="don't keep the EXIF Orientation tag (the image may display rotated)")
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
    risk.add_argument("path", help="file to examine")
    risk.add_argument("--json", action="store_true", help="print the findings as JSON")
    risk.add_argument("--no-color", action="store_true", help="never use colors")
    _add_common_options(risk)
    risk.set_defaults(handler=_cmd_risk)
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

    from metaex.commands.strip import strip_file, summary_lines

    if args.in_place and not args.yes:
        _confirm_overwrite(args.path)
    outcome = strip_file(args.path, args.output, paranoid=args.paranoid,
                         keep_orientation=not args.no_orientation, max_size=args.max_size,
                         in_place=args.in_place, fixed_time=args.fixed_time,
                         rename=args.rename)
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


def _cmd_risk(args: argparse.Namespace) -> int:
    import json

    from metaex.commands.risk import assess_file, risk_lines, risk_to_dict, use_color

    report, findings, strippable = assess_file(args.path, max_size=args.max_size)
    if args.json:
        write_text(json.dumps(risk_to_dict(args.path, report, findings, strippable), indent=2,
                              ensure_ascii=True) + "\n")
    else:
        lines = risk_lines(args.path, report, findings, strippable,
                           color=use_color(args.no_color))
        write_text("\n".join(lines) + "\n")
    return EXIT_OK


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
