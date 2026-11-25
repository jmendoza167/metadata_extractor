# Security policy

metaex reads files from untrusted sources and is used for privacy work, so
security bugs, and cases where metaex says a file is clean when it isn't,
are taken seriously.

## Reporting a vulnerability

Please report vulnerabilities **privately**, through GitHub's private
vulnerability reporting:
[Report a vulnerability](https://github.com/jmendoza167/metadata_extractor/security/advisories/new)
(the "Security" tab of the repository, then "Report a vulnerability").
Don't open a public issue, pull request or discussion for it.

Please include:

- the metaex version (`metaex --version`), your Python version and
  operating system,
- what happens and what you expected,
- a file or a small script that reproduces it. **Never send a personal
  file**: build a test file that shows the problem with made-up data.

This is a project maintained in spare time. I aim to reply within 7 days,
to agree on a fix and a date for publishing it with you, and to credit you
in the advisory and the changelog unless you'd rather not be named.

## Supported versions

Only the latest release gets security fixes.

| Version | Supported |
|---------|-----------|
| 0.1.x   | yes       |

## Threat model

metaex assumes that **every file it reads may be crafted by an attacker**:
a file you received, downloaded or found. What metaex guarantees about such
files:

- **Reading a file never runs code** from it, never contacts the network
  and never needs it: metaex uses only the Python standard library and has
  no network code. It never downloads a DTD, a linked image or anything
  else a file refers to.
- **Nothing is extracted to disk.** Archives and documents are read in
  memory; the only file metaex writes is the cleaned copy, first as a
  temporary file in the destination folder, then renamed into place after
  it was verified.
- **Bounded work.** Files over 512 MiB are refused (`--max-size`), and
  every length, count, nesting depth and decompressed size read from a file
  is capped (archives: 3 levels, 1000 files, and one decompression budget
  of at most 1 GiB or 200 times the file's size), so a crafted file can't
  exhaust memory or make metaex hang.
- **Clean errors.** A damaged or malicious file is refused with a clear
  message; metaex doesn't crash with a traceback and writes nothing for
  that file.
- **XML attacks are refused**: DOCTYPE and ENTITY declarations (except the
  standard SVG DOCTYPE, which is never fetched), external entities.
- **Paths are not trusted**: names that would point outside a folder (`../`,
  absolute paths) and links in archives are refused, symbolic links and
  Windows junctions are never followed in folders, and an output is never
  written over an input file or the original (unless you ask for
  `--in-place`).
- **Untrusted text in output**: CSV cells that could run as spreadsheet
  formulas are prefixed with `'`.
- **Honest results**: `verify` and `strip` say CLEAN only when no metadata
  metaex reads is left and the content matches the original.

## What counts as a vulnerability

- any file that makes metaex crash with an unhandled exception, hang, or
  use memory or time far beyond the limits above;
- writing, deleting or replacing a file other than the cleaned copy, or
  writing outside the destination folder;
- following a link, reading outside the given file or folders, or any
  network access;
- **CLEAN for a file that still holds metadata metaex claims to remove**, or
  a cleaned copy whose content differs from the original while `verify`
  says it matches;
- a CSV or terminal output that runs as a formula or control sequence.

## Out of scope

- What metaex doesn't claim to remove: visible content, data hidden inside
  image or audio data, deleted text in tracked changes, macros, file names
  in archives, and the other items in the README's
  [Limitations](README.md#limitations) and in [FORMATS.md](FORMATS.md).
- Formats metaex refuses (`metaex formats`).
- Files that are dangerous to *open* in another program after cleaning:
  metaex keeps SVG scripts, document macros and links (it warns about
  them); it is not an antivirus.
- Attacks that need control of your computer or of the folders metaex
  writes to, and bugs in Python itself (please report those to the Python
  project).
