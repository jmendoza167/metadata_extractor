# Changelog

All notable changes to metaex are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and metaex uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-25

The first release. metaex shows and removes metadata from photos,
documents, audio files and archives, using only the Python standard library
(Python 3.10 or newer, on Windows, Linux and macOS).

### Added

**Commands**

- `metaex show`: every piece of metadata found, section by section, with
  sensitive fields marked; file system information (dates, owner,
  permissions, the Windows "downloaded from the internet" mark) and MD5 and
  SHA-256 hashes; `--structure` lists the file's segments, chunks or
  blocks; `--extract-thumbnail` saves a JPEG's embedded thumbnail.
- `metaex risk`: what the metadata reveals, grouped as critical, high,
  medium and low, with colors on a terminal (`--no-color`, `NO_COLOR`).
- `metaex strip`: writes a cleaned copy (`<name>_clean.<ext>`), never
  overwriting anything by default; `-o`, `--output-dir`, `--overwrite`,
  `--in-place` (after typing `yes`, or with `--yes`), `--rename` (a random
  name), `--fixed-time`, `--paranoid`, `--no-orientation`,
  `--rebuild-image-data`, `--remove-comments` and `--deep`.
- `metaex verify`: checks that a file has no metadata left and, with
  `--original`, that its image, document, audio or archive content matches
  the original. Exit code 3 when it is not clean.
- `metaex formats`: the supported formats, what `strip` removes from each,
  and the formats metaex refuses, with the reason.
- `--json` for every command; `--csv` for `show`, `risk` and `verify`.
- A file type is recognized by its content, not its name, and a name that
  doesn't match the content is pointed out.

**Images**: JPEG, PNG, GIF, WebP and SVG

- Reads EXIF (with GPS and a map link, serial numbers and the embedded
  thumbnail), XMP and Extended XMP, IPTC and Photoshop resources, ICC
  profiles, MPF extra images, comments, PNG text chunks and AI generation
  data (Stable Diffusion, ComfyUI, InvokeAI), Content Credentials, GIF and
  WebP extensions and chunks, SVG `<metadata>`, editor data and local file
  paths, embedded images inside SVG, and data after the end of an image.
- Finds data hidden inside a PNG's compressed image stream;
  `--rebuild-image-data` rebuilds it with identical pixels.
- Strips with a whitelist and never re-encodes the image: the image data is
  copied byte for byte (SVG is written again from the parsed drawing). The
  orientation and the color profile are kept unless you ask
  (`--no-orientation`, `--paranoid`).

**Office documents**: DOCX, XLSX, PPTX (with the macro-enabled and template
variants) and ODT, ODS, ODP

- Reads document, application and custom properties, sensitivity labels,
  SharePoint data, comments and their authors, tracked changes and deleted
  text still in the file, revision IDs, file paths and links, hidden
  content, embedded images and objects, macros, printer settings and the
  thumbnail.
- Strips them by editing only the bytes that hold metadata; comment and
  revision authors become "Author" (`--remove-comments` removes comments);
  embedded images are cleaned like separate images. Warns about tracked
  changes, macros, hidden content and links, which it keeps.

**Audio**: MP3, WAV and FLAC

- Reads ID3v2 (2.2 to 2.4), ID3v1, APE and Lyrics3 tags, RIFF INFO, Broadcast
  WAV `bext`, iXML, axml, Vorbis comments, pictures and cover art (read with
  the image readers), cue points and cue sheets, and data hidden in padding
  or between audio frames.
- Removes every tag and keeps the audio byte for byte; `--paranoid` also
  removes the MP3 Xing/LAME frame, WAV cue points and loops and the FLAC
  cue sheet.

**Archives**: ZIP, TAR, gzip, bzip2 and xz

- Reads entry times, owners, permissions, comments, extended attributes
  (including macOS download records), files the operating system adds
  (`__MACOSX/`, `._` files, `.DS_Store`, `Thumbs.db`, `desktop.ini`), names
  that reveal a user folder, gzip headers, hidden data, and the metadata of
  the files inside (up to 3 levels of archives inside archives).
- Rewrites the archive with neutral headers and keeps every file, its name
  and its contents; `--deep` also cleans the files inside, each with its
  own format's rules.

**Many files and folders**

- `show`, `risk`, `strip` and `verify` accept several files and folders;
  `--recursive` searches subfolders, `--include-hidden` includes hidden
  files. Symbolic links are never followed, and hidden files, version
  control and dependency folders and metaex's own cleaned copies are
  skipped.
- Every file gets an outcome (cleaned, already clean, skipped, refused,
  error; clean or not clean for `verify`); one bad file never stops the
  run, and Ctrl+C stops after the current file.
- A summary after every run, `[i/N]` progress lines and `--quiet`.
- `--csv` writes one row per finding, as UTF-8 with a byte order mark and
  with protection against spreadsheet formulas.

**Safety**

- Unsupported formats are refused, never "cleaned", with the detected type
  and the reason.
- Every cleaned copy is written to a temporary file, verified, and only then
  moved into place; damaged files are refused.
- Every file is treated as untrusted: reads are bounds-checked, sizes,
  counts, nesting and decompression are capped (files over 512 MiB are
  refused; `--max-size` changes that), XML DOCTYPE and entity declarations
  are refused, and nothing is ever extracted to disk.

[Unreleased]: https://github.com/jmendoza167/metadata_extractor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jmendoza167/metadata_extractor/releases/tag/v0.1.0
