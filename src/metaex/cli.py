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
    "strip": "write a copy of a file with metadata removed",
    "verify": "check that a cleaned file contains no metadata",
}


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
        "--max-size",
        type=_size_in_mib,
        default=DEFAULT_MAX_SIZE,
        metavar="MB",
        help=f"refuse files larger than this many MiB (default {DEFAULT_MAX_SIZE // MIB})",
    )
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
    show.add_argument("--debug", action="store_true", help="show tracebacks on errors")
    show.set_defaults(handler=_cmd_show)

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
