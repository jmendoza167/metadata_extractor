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

# Subcommand name -> one-line help text for commands not implemented yet.
PLACEHOLDERS = {
    "risk": "summarize the privacy risk of a file's metadata",
    "verify": "check that a cleaned file contains no metadata",
}


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
    strip.add_argument("--yes", action="store_true",
                       help="don't ask for confirmation with --in-place")
    strip.add_argument("--paranoid", action="store_true",
                       help="also remove the ICC color profile and JFIF density "
                            "(colors may shift)")
    strip.add_argument("--no-orientation", action="store_true",
                       help="don't keep the EXIF Orientation tag (the image may display rotated)")
    strip.add_argument("--json", action="store_true", help="print the summary as JSON")
    _add_common_options(strip)
    strip.set_defaults(handler=_cmd_strip)

    for name, help_text in PLACEHOLDERS.items():
        sub = subparsers.add_parser(name, help=help_text, description=help_text)
        sub.set_defaults(handler=_not_implemented)
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
                         in_place=args.in_place)
    if args.json:
        write_text(json.dumps(outcome.to_dict(), indent=2, ensure_ascii=True) + "\n")
    else:
        write_text("\n".join(sanitize(line) for line in summary_lines(outcome)) + "\n")
    return EXIT_OK


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


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"metaex {args.command}: not implemented yet", file=sys.stderr)
    return EXIT_ERROR


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
