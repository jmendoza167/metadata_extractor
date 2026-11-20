"""Command-line interface for metaex."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from metaex import __version__

# Subcommand name -> one-line help text. All are placeholders for now.
SUBCOMMANDS = {
    "show": "show the metadata found in a file",
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
    for name, help_text in SUBCOMMANDS.items():
        sub = subparsers.add_parser(name, help=help_text, description=help_text)
        sub.set_defaults(handler=_not_implemented)
    return parser


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"metaex {args.command}: not implemented yet", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run the metaex CLI and return a process exit code.

    argparse handles --help, --version and usage errors by raising
    SystemExit; that is caught here so main() always returns an int and
    only the top-level entry point exits the process.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return _exit_code(exc.code)

    if args.command is None:
        parser.print_help()
        return 0

    return args.handler(args)


def _exit_code(code: object) -> int:
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1
