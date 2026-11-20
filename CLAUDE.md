# CLAUDE.md

Guidance for working on **metaex** in this repository.

## Project purpose

metaex is a command-line tool that **shows** and **removes** metadata from
files (photos, documents, audio, archives) for privacy. It is written in
Python and uses **only the standard library at runtime**. pytest is the only
dev dependency.

It is used for real privacy work and published as a public repo, so
**correctness and honesty matter more than features**. Never claim a file is
clean unless it is; never claim support for a format that isn't implemented.

## Core principles

1. **Stripping uses a whitelist.** Keep only what a format needs to render;
   drop everything else, including unknown or unrecognized data. Never use a
   blacklist ("remove these known tags") approach.
2. **Unsupported formats are refused** with a clear message, never "cleaned".
   If metaex doesn't understand a file, it says so and writes nothing.
3. **Never overwrite the original by default.** Output goes to
   `<name>_clean.<ext>` next to the input.
4. **Parsers are defensive.** Every file is untrusted:
   - bounds-check every read,
   - cap sizes (lengths, counts, nesting depth, decompressed sizes),
   - raise clean, specific errors on malformed input,
   - never crash with an unhandled exception and never hang (no unbounded
     loops driven by file data).
5. **One module per format** in `src/metaex/formats/`, each exposing:
   - `parse(data: bytes) -> dict` — the metadata found, for display
   - `strip(data: bytes) -> bytes` — a cleaned copy built from the whitelist

## Layout

```
src/metaex/
    __init__.py      # __version__
    __main__.py      # enables `python -m metaex`
    cli.py           # argparse entry point; main() returns an exit code
    formats/         # one module per file format
tests/               # pytest tests
```

- `main()` returns an integer exit code; only the top-level entry points call
  `sys.exit`. Don't call `sys.exit` deep in the code.
- No runtime dependencies. Do not add anything to `dependencies` in
  `pyproject.toml`.

## Development setup and tests

```
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

- **Every new feature needs tests.** Bug fixes need a regression test.
- Tests that run the CLI use `sys.executable -m metaex` so they work on
  Windows, Linux and macOS.
- Parser tests should include malformed/truncated/oversized inputs, not just
  happy paths.

## Commit rules

- Small, focused commits; only stage the files that belong to the change.
- Use conventional-style messages (`feat:`, `fix:`, `test:`, `docs:`,
  `chore:`, `ci:`).
- Run `pytest` before committing; don't commit with failing tests.
- **Commit messages must not contain `Co-Authored-By` lines or any
  Claude/AI attribution.**
