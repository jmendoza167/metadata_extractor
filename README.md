# metaex

**metaex** is a command-line tool that shows and removes metadata from
files: photos, documents, audio and archives. It is written in Python and
uses only the standard library, so there is nothing to install besides
Python itself.

> **Status: work in progress.** metaex does not process any file format
> yet. Everything under "Planned" below describes what is being built, not
> what works today. Do not rely on it for privacy yet.

## Why metadata matters for privacy

Files carry more than the content you see. Depending on the format and the
software that made them, they can contain:

- **GPS coordinates** of where a photo was taken
- **device details**: camera or phone make, model and serial number
- **timestamps** for when a file was created, edited or printed
- **names and usernames** of authors, editors and commenters
- **software versions**, computer names and file paths (which often include
  a username)
- **edit history**, tracked changes, comments and embedded thumbnails that
  can show content you cropped or deleted

Sharing a file shares all of this. metaex aims to let you see it and remove
it before you share.

## Design goals

- **Whitelist stripping.** When cleaning, metaex keeps only the data a
  format needs to display the file and drops everything else, including
  data it doesn't recognize.
- **Refuse what it doesn't understand.** Unsupported formats are rejected
  with a clear message. They are never "cleaned".
- **Never overwrite the original.** Cleaned output is written to
  `<name>_clean.<ext>` by default.
- **Defensive parsing.** Every input file is treated as untrusted. Malformed
  files produce a clear error instead of a crash or a hang.
- **No runtime dependencies.** Standard library only.

## Planned supported formats

None of these are implemented yet.

| Category  | Formats                 |
|-----------|-------------------------|
| Images    | JPEG, PNG, GIF, WebP, SVG |
| Documents | DOCX, XLSX, PPTX, ODT   |
| Audio     | MP3, WAV, FLAC          |
| Archives  | ZIP, TAR                |

## Install

There is no release on PyPI yet. To install from source (Python 3.10+):

```
git clone https://github.com/jmendoza167/metadata_extractor.git
cd metadata_extractor
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -e .
metaex --version
```

## Planned usage

These commands exist, but they currently only print "not implemented yet".
The examples show the intended behavior.

```
metaex show photo.jpg      # list the metadata in a file
metaex risk photo.jpg      # summarize what that metadata reveals
metaex strip photo.jpg     # write photo_clean.jpg without metadata
metaex verify photo_clean.jpg   # check that a cleaned file has no metadata left
```

## Limitations

Removing metadata does not make a file anonymous. No metadata tool can
remove:

- **Visible content**: faces, street signs, screens, reflections, or names
  and addresses written in the document itself.
- **Hidden document text**: text in white-on-white, text behind images, or
  content covered by a shape or "redaction" box is part of the document, not
  metadata.
- **Sensor fingerprints**: camera sensors leave a noise pattern (PRNU) in
  every photo that can link images to the same device.
- **Watermarks**: visible or invisible watermarks embedded in the image,
  audio or text itself.
- **Platform-side data**: anything a website, app or messaging service
  records when you upload a file (account, IP address, upload time), and any
  copy it already has.

## Development

```
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

### Secret scanning (gitleaks)

metaex needs no tokens or keys, and the repository must never contain any.
[gitleaks](https://github.com/gitleaks/gitleaks) checks this. It is a
development tool only; metaex itself has no dependencies.

1. Install gitleaks: `winget install Gitleaks.Gitleaks` (Windows),
   `brew install gitleaks` (macOS), or a release binary from
   <https://github.com/gitleaks/gitleaks/releases> (Linux).
2. Install the pre-commit hook (once per clone):

   ```
   python scripts/install_hooks.py
   ```

   The hook scans staged changes with `--redact` and blocks the commit if
   gitleaks finds a possible secret, fails, or is not installed. The
   installer never overwrites an existing, different pre-commit hook.
3. Before pushing, scan the full history:

   ```
   gitleaks git --redact --verbose .
   ```

   (gitleaks older than 8.19: `gitleaks detect --redact --verbose`.)
   If anything is found, don't push.

Test files are always generated in code; never commit real photos or
documents. Keep real samples for manual testing in `private_samples/`, which
is git-ignored.

## License

MIT. See [LICENSE](LICENSE).
