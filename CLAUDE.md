# CLAUDE.md

Guidance for working on **metaex** in this repository.

## Project purpose

metaex is a command-line tool that **shows** and **removes** metadata from
files (photos, documents, audio, archives) for privacy. It is written in
Python and uses **only the standard library at runtime**. The only dev
dependencies are pytest, Pillow, python-docx, openpyxl, python-pptx and
mutagen: independent readers for the tests' pixel checks, Office content
checks and audio tag checks. metaex itself must never import them (or
lxml, which some of them use); a test enforces this.

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
- **Office documents (OOXML and OpenDocument) are edited, not rewritten.**
  ZIP entries that don't change are copied with their compressed bytes
  as they are; only the ZIP headers are new (fixed 1980-01-01 time, no
  extra fields, no comments; `[Content_Types].xml` or a stored `mimetype`
  first). An XML part that changes is edited as byte splices on the
  original (core/xmldoc.py `XmlEditor`): every namespace declaration and
  prefix (including those in `mc:Ignorable`), quote and space outside the
  edits stays exactly as it was; parts that don't change are never
  re-serialized. Removing a part also removes its relationships and its
  content type override (OpenDocument: its manifest entry). Embedded images
  go through the image strippers. Verification compares every part: same
  bytes, or the same canonical XML that leaves out only what strip may
  change, or the same image data. Where a format requires a date, strip
  writes neutral dates from 2000-01-01T00:00:00 in one-second steps that
  keep the original order (`ooxml.neutral_dates`).
- **Audio (MP3, WAV, FLAC) loses every tag; the audio stays byte for
  byte.** MP3: every audio frame in order (not the bytes between them).
  WAV: `fmt` and `data` (and the `fact` sample count). FLAC: STREAMINFO,
  SEEKTABLE and every byte from the first frame to the end of the last.
  Kept by default because players use them, removed with `--paranoid`: the
  MP3 Xing/Info/VBRI frame (its unknown bytes zeroed), WAV `cue`/`smpl`
  (rebuilt from their positions, without labels or sampler identity) and
  the FLAC CUESHEET (reserved bytes zeroed). Non-zero padding and data
  between or after frames is hidden data: reported as critical and always
  removed. Verification compares the frames (or `fmt`/`data`, or
  STREAMINFO and frames) with the original, and PCM WAV frames also with
  the stdlib `wave` module.
- **Archives (ZIP, TAR, gzip, bzip2, xz) keep every file, its name and its
  contents.** A cleaned archive is rewritten with neutral headers (time
  1980-01-01 00:00, owner 0 with no names, permissions 644/755 with the
  executable bit kept, no extra fields, comments, pax records other than
  long names, or hidden bytes); operating system junk (`__MACOSX/`,
  AppleDouble `._` files, `.DS_Store`, `Thumbs.db`, `desktop.ini`) and
  earlier copies replaced by a later TAR entry are removed. ZIP entries
  keep their compressed bytes; a gzip, bzip2 or xz file is compressed
  again only when what it holds changed. Files inside are cleaned only with
  `--deep`, each by its own format module and verified against the
  original file inside; files metaex can't clean are copied and listed,
  never dropped. Without `--deep`, strip warns about files inside that
  still hold metadata, and `verify` reports them (NOT CLEAN). Verification
  compares the entries (names, types, link targets) and every file's bytes,
  or for `--deep` output each cleaned file with its original. Reading
  inside archives is bounded: 3 levels of nesting, 1000 files, and one
  decompression budget shared by everything read from the file.

## Many files (batch runs)

- **Batch runs reuse the single-file code**: every file goes through
  `build_report`, `assess_file`, `strip_file` or `verify_file` exactly as
  when it is named alone (same temp file, verification and rename). A run
  with one path that is not a folder keeps the single-file output and exit
  codes.
- **One bad file never stops a run**: each file gets an outcome (cleaned,
  already clean, clean, not clean, examined, skipped, refused, error) and
  the run goes on; Ctrl+C stops after the current file with a partial
  summary. Exit codes: 1 if any file was refused or failed, else 3 if
  `verify` found a file not clean, else 0.
- **Never write over an input**: outputs are checked against every input
  file of the run and earlier outputs of the run (`core/batch.path_key`:
  resolved, case-folded on Windows and macOS); `--output-dir` may not be an
  input folder; `--overwrite` never replaces an original.
- **Never follow links** (symbolic links, Windows junctions) and keep the
  skip rules in `core/batch.py` (hidden files, `.git`, `node_modules`,
  virtual environments, `*_clean.*`, the output folder). Files are processed
  in sorted order so output is the same on every system.
- **CSV cells are untrusted**: every cell goes through `csvout.protect()`.

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
    samples/         # committed sample files (see "Sample files" below)
scripts/             # dev tools: install_hooks.py, make_samples.py
```

What belongs where:

- **`core/`**: code shared by many formats and commands, with no CLI or
  format-specific knowledge: errors (`errors.py`), bounds-checked reading
  (`reader.py`), size-limited loading (`loader.py`), type detection
  (`filetype.py`), file system metadata and hashes (`generic.py`), the
  report data model and rendering (`result.py`, `report.py`), and
  metadata structures used by several formats, such as EXIF/TIFF
  (`exif.py`, reused by JPEG, PNG, WebP and TIFF). For office documents:
  the safe ZIP reader and deterministic writer (`ziparchive.py`,
  `zipwriter.py`), XML parsing with byte offsets and minimal edits
  (`xmldoc.py`), OOXML packages and their verification (`ooxml.py`,
  `ooxml_verify.py`, shared by DOCX, XLSX and PPTX), OpenDocument packages
  (`odf.py`, `odf_verify.py`, shared by ODT, ODS and ODP), the OLE2
  directory reader that recognizes legacy and encrypted Office files
  (`ole2.py`) and local path detection (`paths.py`). For audio: ID3v2 and
  ID3v1 tags (`id3.py`, used by MP3, WAV and FLAC), APE and Lyrics3 tags
  and the scanner for tags at the end of a file (`audiotags.py`), pictures
  read with the image parsers (`embedded.py`) and CRC-8/CRC-16 (`crc.py`).
  For archives: archive members, their report sections, operating system
  junk, link checks, reading and cleaning the files inside (`archive.py`),
  AppleDouble files and binary property lists (`appledouble.py`), the TAR
  reader and writer (`tar.py`, `tarwriter.py`), and gzip, bzip2 and xz
  files (`compress.py`, shared by `formats/gzip.py`, `bzip2.py` and `xz.py`).
  For many files: collecting the files from paths and folders, the skip
  rules and path comparisons (`batch.py`); CSV output with formula
  protection (`csvout.py`).
- **`commands/`**: the logic of each subcommand (`show.py`, ...). Builds a
  report from `core/` and `formats/`; no argparse here. `batch.py` runs a
  command on many files (outcomes, summary, exit codes); `formats.py` is
  `metaex formats`.
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
python -m pytest
```

- **Every new feature needs tests.** Bug fixes need a regression test.
- **Never commit real personal files** (photos, documents, recordings),
  not even as test fixtures: they carry exactly the metadata this tool
  exists to remove. Keep real samples for manual testing in
  `private_samples/`, which is git-ignored. Tests only use files generated
  in code (see `tests/helpers/`) and the committed samples described below.
- Tests that run the CLI use `sys.executable -m metaex` so they work on
  Windows, Linux and macOS.
- Parser tests should include malformed/truncated/oversized inputs, not just
  happy paths.
- **Run the tests as `python -m pytest`**, not the `pytest` launcher: on
  Windows machines with antivirus software the `pytest.exe` launcher can be
  blocked or slowed down, and `python -m pytest` also makes sure the
  venv's Python is the one that runs.
- **Never commit after a pytest run whose exit code you didn't see.** Piping
  the output through `tail`, `head` or `grep` hides pytest's exit code (the
  pipeline reports the last command's). Save the output to a file and check
  `$?` (or `$LASTEXITCODE`) of pytest itself before committing.
- **Never compare compressor output bytes across machines.** zlib, bz2 and
  lzma output depends on the library build (classic zlib and zlib-ng
  compress the same data differently). Tests compare decompressed content,
  or use data whose compressed form is built by hand (stored deflate
  blocks, uncompressed LZMA2 chunks, a hand-written encoder).

## Sample files

- `tests/samples/` holds committed samples, documented in
  `tests/samples/SAMPLES.md`. Everything there except `SAMPLES.md`,
  `expected/` and `real/` is generated by `scripts/make_samples.py` from
  the test helpers (never with Pillow), with made-up metadata only, and is
  byte-identical on every machine (PNG zlib data uses stored blocks,
  Office and ZIP entries are stored, gzip samples use stored deflate
  blocks, bzip2 and xz samples are written by `tests/helpers/bzip2.py` and
  `xz.py`, and the bomb samples use a hand-written deflate stream; never
  use zlib-, bz2- or lzma-compressed output in a sample; audio
  samples are computed with integers only, never with floating point). Never
  edit those files by hand: change the generator, run
  `python scripts/make_samples.py` and commit the result. The tests run
  `--check`, which fails on a missing, changed or extra file.
- `tests/samples/expected/` holds snapshots of `show --json` and
  `risk --json` for every valid sample. They are only ever updated on
  purpose, with `METAEX_UPDATE_SNAPSHOTS=1 python -m pytest tests/test_samples_snapshots.py`;
  review the diff before committing, and never update them just to make a
  failing test pass without understanding why the output changed.
- `tests/samples/real/` is the only place for files saved by real software.
  It may only hold files made from scratch for this repository, after
  clearing the maker's identity with the checklist in `SAMPLES.md`; never a
  personal file. The sample tests include it; `--check` ignores it.

## Documentation

- **README.md is for users only**: what metaex does, how to install and
  use it, examples and limitations. Write it in user-facing language.
- **Development setup belongs here, in CLAUDE.md, never in the README**:
  the venv, dev dependencies, running the tests, gitleaks and the git
  hooks (`scripts/install_hooks.py`).

## Git workflow and commit rules

- Small, focused commits; only stage the files that belong to the change.
- Use conventional-style messages (`feat:`, `fix:`, `test:`, `docs:`,
  `chore:`, `ci:`).
- Run `python -m pytest` before committing and check its exit code; don't commit with
  failing tests.
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
- `tests/samples/malformed/` is marked `-diff` in `.gitattributes`: git
  shows those files as binary, so gitleaks doesn't scan them (and Git for
  Windows doesn't run its docx2txt converter on the broken ones, which
  printed ERR lines in scans). Keep that rule to this one folder; everything
  else is scanned (`tests/test_gitleaks_scope.py` checks both).
- **Before every push**: run gitleaks on the full history with `--redact`;
  if anything is found, don't push.
- **If a secret is ever found** in the repo or its history: stop and tell
  Javier immediately. Don't try to rewrite or fix history yourself.
