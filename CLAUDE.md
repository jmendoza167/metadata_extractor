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

## Stripping guarantees

These hold for every format metaex strips:

- **The image is never re-encoded.** Image-defining data is copied
  byte-for-byte; for JPEG that is the entropy-coded scan data and the tables
  (DQT, DHT, SOF, SOS, DRI).
- **Output is written to a temp file in the destination folder, verified,
  and only then renamed into place** (`os.replace`). If verification fails,
  the temp file is deleted and nothing is left behind.
- **Malformed input is refused, never "partly stripped".**
- **SVG is the one exception to byte-for-byte copying.** SVG is XML text, so
  a cleaned SVG is *re-serialized*: parsed (with expat, no namespace
  processing) and written again from the parsed tree. Elements, attributes
  and text that are kept come out as parsed, and namespace prefixes stay
  exactly as written: the default SVG namespace and `xlink:` keep their
  declarations, and there are never generated prefixes like `ns0:`. What
  may change is formatting: attribute quoting and escaping, whitespace
  inside tags, `<a></a>` written as `<a/>`, the indentation left by removed
  elements (outside text), and character references written as characters.
  Embedded data: URI images are stripped by their own format's rules and
  re-encoded as base64. Verification compares a canonical form of the
  drawing instead of bytes.

## Layout

```
src/metaex/
    __init__.py      # __version__
    __main__.py      # enables `python -m metaex`
    cli.py           # argparse entry point; main() returns an exit code
    core/            # format-independent building blocks
    commands/        # one module per CLI subcommand
    formats/         # one module per file format
tests/               # pytest tests
    helpers/         # builders that generate test files in memory
```

What belongs where:

- **`core/`**: code shared by many formats and commands, with no CLI or
  format-specific knowledge: errors (`errors.py`), bounds-checked reading
  (`reader.py`), size-limited loading (`loader.py`), type detection
  (`filetype.py`), file system metadata and hashes (`generic.py`), the
  report data model and rendering (`result.py`, `report.py`), and
  metadata structures used by several formats, such as EXIF/TIFF
  (`exif.py`, reused by JPEG, PNG, WebP and TIFF).
- **`commands/`**: the logic of each subcommand (`show.py`, ...). Builds a
  report from `core/` and `formats/`; no argparse here.
- **`formats/`**: one module per file format (`jpeg.py`, ...), each
  exposing `parse(data) -> dict` and `strip(data) -> bytes`. A format
  module may use `core/`, never `commands/` or `cli.py`.
- **`cli.py`**: argument parsing, printing and turning errors into exit
  codes only.

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
- **Never commit real personal files** (photos, documents, recordings),
  not even as test fixtures: they carry exactly the metadata this tool
  exists to remove. Keep real samples for manual testing in
  `private_samples/`, which is git-ignored. Tests only use files generated
  in code (see `tests/helpers/`).
- Tests that run the CLI use `sys.executable -m metaex` so they work on
  Windows, Linux and macOS.
- Parser tests should include malformed/truncated/oversized inputs, not just
  happy paths.

## Git workflow and commit rules

- Small, focused commits; only stage the files that belong to the change.
- Use conventional-style messages (`feat:`, `fix:`, `test:`, `docs:`,
  `chore:`, `ci:`).
- Run `pytest` before committing; don't commit with failing tests.
- **Commit messages must not contain `Co-Authored-By` lines or any
  Claude/AI attribution.**
- **Claude Code commits but NEVER pushes.** Javier pushes himself.
- At the end of each milestone:
  1. run `git log -i --grep="co-authored-by" --oneline` (must print nothing),
  2. run the full-history gitleaks scan with `--redact` (see "Secrets"),
  3. if both are clean, report that the milestone is ready to push.

## Secrets

- This project needs **no tokens or API keys**. Never add code that uses,
  requests or stores them.
- **Never read, print or copy credential files**: `.env` files,
  `~/.config/gh`, `~/.pypirc`, SSH keys, the Windows credential store, or
  anything similar. Never run `gh auth token` or any other command that
  prints a token.
- **Never put real tokens** in tests, docs, examples or commit messages.
  Fake tokens in tests must be built at runtime (e.g. by joining pieces),
  so that no literal token pattern exists anywhere in the source.
- gitleaks runs before every commit (pre-commit hook, installed with
  `python scripts/install_hooks.py`) and in CI.
- **Before every push**: run gitleaks on the full history with `--redact`;
  if anything is found, don't push.
- **If a secret is ever found** in the repo or its history: stop and tell
  Javier immediately. Don't try to rewrite or fix history yourself.
