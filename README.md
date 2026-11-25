# metaex

[![tests](https://github.com/jmendoza167/metadata_extractor/actions/workflows/tests.yml/badge.svg)](https://github.com/jmendoza167/metadata_extractor/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/metaex)](https://pypi.org/project/metaex/)
[![Python](https://img.shields.io/pypi/pyversions/metaex)](https://pypi.org/project/metaex/)

**metaex** shows and removes metadata from photos, documents, audio files
and archives before you share them. It tells you what a file gives away
(GPS position, camera serial number, author names, deleted text, the
computer it was made on), writes a cleaned copy, and checks that the copy
really is clean and looks, reads or plays exactly like the original.

It is a command-line program written in Python, using only the standard
library: nothing to install besides Python 3.10 or newer, on Windows, Linux
and macOS.

## Why metadata matters

Files carry more than the content you see. Depending on the format and the
program that made them, they can contain:

- **GPS coordinates** of where a photo was taken,
- **device details**: camera or phone make, model and serial number,
- **dates** a file was created, edited or printed,
- **names and accounts** of authors, editors and commenters,
- **software versions**, computer names and file paths (which often include
  your user name),
- **edit history**, tracked changes with deleted text, comments, and
  thumbnails that can show what you cropped out.

Sharing a file shares all of this.

## How metaex cleans

- **Whitelist.** metaex keeps only what a format needs to show or play the
  file and drops everything else, including data it doesn't recognize.
- **No quality loss.** Images are never re-encoded and audio is never
  decoded: the content is copied byte for byte.
- **Checked before it exists.** The cleaned copy is written to a temporary
  file, read again from scratch and compared with the original. Only then
  is it saved; if anything is wrong, nothing is written.
- **Your original is never touched** unless you ask (`--in-place`). The
  cleaned copy is `<name>_clean.<ext>`.
- **Refuses what it doesn't understand.** Unsupported or damaged files are
  refused with an explanation, never "cleaned".
- **Safe with untrusted files.** Every file is treated as hostile: no
  network access, nothing extracted to disk, and limits on sizes and
  nesting. See [SECURITY.md](https://github.com/jmendoza167/metadata_extractor/blob/main/SECURITY.md).

## Supported formats

| Kind | Formats |
|------|---------|
| Images | JPEG, PNG, GIF, WebP, SVG |
| Office documents | DOCX, XLSX, PPTX (and .docm, .xlsm, .pptm, templates); ODT, ODS, ODP |
| Audio | MP3, WAV, FLAC |
| Archives | ZIP, TAR, gzip, bzip2, xz (.tar.gz, .tgz, .tar.bz2, .tar.xz, ...) |

`show`, `risk`, `strip` and `verify` work on all of them.
[FORMATS.md](https://github.com/jmendoza167/metadata_extractor/blob/main/FORMATS.md)
explains, format by format, what metaex reads, what it removes, what it
keeps on purpose and its known limits; `metaex formats` prints a short
version. Other formats (PDF, HEIC, TIFF, video, legacy .doc/.xls/.ppt,
password-protected documents, ...) are refused.

## Install

```
pip install metaex
```

or, to keep it apart from other Python packages, `pipx install metaex`.
Then check it works:

```
metaex --version
```

## Quick start

```
metaex risk photo.jpg                                # what the file reveals
metaex strip photo.jpg                               # write photo_clean.jpg
metaex verify photo_clean.jpg --original photo.jpg   # check the cleaned copy
metaex show photo.jpg                                # every field, in detail
metaex formats                                       # supported formats
```

Every command also takes several files and folders (see
[Many files and folders](#many-files-and-folders)). `metaex <command>
--help` lists every option.

The examples below are real output, made with the sample files of the
[test suite](https://github.com/jmendoza167/metadata_extractor/blob/main/tests/samples/SAMPLES.md);
all their metadata is made up. Some long lists are shortened (marked
`...`).

### risk: what does this file reveal?

```
$ metaex risk photo.jpg
metaex risk: photo.jpg
20 critical, 17 high, 8 medium, 28 low

Notes:
  ! embedded thumbnail may show the original, uncropped image
  ! Photoshop thumbnail present: it may show an earlier, uncropped or unedited version of the image

CRITICAL (20)
  EXIF: BodySerialNumber                    SN-0001
  GPS: Coordinates                          48.858367, 2.294481
  GPS: Latitude                             48.858367
  GPS: Longitude                            2.294481
  GPS: OpenStreetMap                        https://www.openstreetmap.org/?mlat=48.858367&mlon=2.294481#map=16/48.858367/...
  GPS: Google Maps                          https://www.google.com/maps?q=48.858367,2.294481
  GPS: Altitude                             35.0 m above sea level
  Thumbnail: Embedded thumbnail             JPEG, 123 bytes
  ...

HIGH (17)
  EXIF: Make                                ExampleCam
  EXIF: Model                               Model X
  EXIF: DateTimeOriginal                    2024-05-01T14:30:00+02:00
  XMP: dc:creator                           Jane Doe
  IPTC: City                                Paris
  ...

MEDIUM (8)
  EXIF: ExposureTime                        1/250 s
  ICC profile: Device manufacturer          ACME
  ...

LOW (28)
  ...

Run "metaex strip photo.jpg" to remove these.
```

**Critical** findings point to you or a place (GPS, serial numbers, owner
and author names, account IDs, local file paths, thumbnails, deleted text,
hidden data); **high** ones describe you or your devices (make and model,
dates, software, comments, edit history); **medium** and **low** are
technical details. On a terminal the levels are colored (`--no-color` or
the `NO_COLOR` variable turns that off).

### strip: write a cleaned copy

```
$ metaex strip photo.jpg
metaex strip: photo.jpg
Wrote photo_clean.jpg (1.2 KiB; original 5.0 KiB)

Removed:
  Camera and owner                3 fields: Make, Model, BodySerialNumber
  Camera settings and other EXIF  6 fields: Orientation, ExposureTime, FNumber, ISOSpeedRatings, ...
  Software                        1 field: Software
  Dates                           3 fields: DateTime, DateTimeOriginal, OffsetTimeOriginal
  GPS location                    8 fields: Coordinates, Latitude, Longitude, OpenStreetMap, ...
  Thumbnail                       4 fields: Embedded thumbnail, Dimensions, Compression, 0x040C Photoshop thumbnail
  XMP                             26 fields: XMP packet, xmp:CreatorTool, xmpMM:DocumentID, xmpMM:OriginalDocumentID, ...
  IPTC                            4 fields: CodedCharacterSet, By-line, City, Keywords
  Photoshop resources             1 field: 0x0404 IPTC-NAA record
  Extra images (MPF)              123 B
  Comments                        1 field: Comment 1
  Other APP segments              1 field: APP12 "Ducky"
  Data after end of image         138 B

Kept by design:
  ICC profile: kept so colors stay correct (description: "Test RGB profile")
  JFIF: Version = 1.02, Density = 72 x 72 dpi
  Adobe: DCT encode version = 100, Color transform = YCbCr
  Warning: ICC profile names a device (ACME X100); use --paranoid to remove it (colors may shift)

Verification: CLEAN
  Image data unchanged (SHA-256 cbe659a3289989ba9faef835d6493ac530fbf3957ae2cae412e32b2f4308c4cb)

File timestamps: new (not copied from the original)
Windows Zone.Identifier stream: none (no download origin attached)
```

"Kept by design" is what metaex keeps on purpose because removing it would
change how the file looks or plays (here the color profile); `--paranoid`
removes that too.

Documents work the same way, and metaex says loudly what it can't fix for
you:

```
$ metaex strip letter.docx
metaex strip: letter.docx
Wrote letter_clean.docx (9.3 KiB; original 20.9 KiB)

*** WARNING ***
the document still contains tracked changes, and 14 characters of DELETED TEXT are still stored in the file, where anyone can read them. Accept or reject all changes in Word (Review > Accept > Accept All Changes) or LibreOffice (Edit > Track Changes > Accept All), save, and run metaex again.

Removed:
  Document properties             15 fields: Title, Subject, Author, Keywords, ...
  Application properties          20 fields: Template, Total editing time (minutes), Pages, Words, ...
  Comment authors and dates       4 fields: Comment authors "Jane Doe" (replaced with "Author"), Comment author initials, Comment dates, Comment dates (UTC)
  Revision authors and dates      2 fields: Revision authors "Jane Doe, John Example" (replaced with "Author"), Revision dates
  Local file paths                1 field: Attached template (word/settings.xml)
  GPS location                    8 fields: Coordinates, Latitude, Longitude, OpenStreetMap, ...
  Printer settings                1 field: word/printerSettings/printerSettings1.bin
  ...

Kept by design:
  Comments: Comments = 1, Comment authors = Author
  Hidden content: Hidden text (runs) = 1, Hidden text = Secret note to self, ...
  External links: Hyperlink (word/document.xml) = https://example.com/flat
  Warning: kept 1 comment(s) (their authors are now "Author"); use --remove-comments to remove them
  ...

Verification: CLEAN
  Document content unchanged (SHA-256 c8445705967bf84f23ebff4e32cda57f3bcddbe09bd70cb7f6d12bf1adc0e348)
...
```

Options for `strip`:

| Option | What it does |
|--------|--------------|
| `-o FILE` | where to write the cleaned copy (one file only) |
| `--output-dir DIR` | write cleaned copies into `DIR`, with their original names |
| `--overwrite` | replace a cleaned copy that already exists (never an original) |
| `--in-place` | replace the original, after you type `yes` (`--yes` skips the question); refused for a symbolic link or a file with several hard links, whose other names would keep the original |
| `--rename` | give the copy a random name, like `img_8f3a2c1d.jpg` |
| `--paranoid` | also remove what is kept by design: color profiles, MP3 encoder frame, WAV cue points, FLAC cue sheet, SVG title and description |
| `--no-orientation` | don't keep the image rotation |
| `--remove-comments` | office documents: remove comments instead of renaming their authors to "Author" |
| `--deep` | archives: also clean the files inside |
| `--rebuild-image-data` | PNG: remove data hidden inside the compressed image (pixels stay identical) |
| `--fixed-time` | set the copy's modified time to 2000-01-01 |

### verify: check a cleaned file

```
$ metaex verify photo_clean.jpg --original photo.jpg
metaex verify: photo_clean.jpg
Result: CLEAN

Kept by design:
  ICC profile: Size = 244 bytes
  ICC profile: Device manufacturer = ACME
  ICC profile: Device model = X100
  ...

Image data: matches the original (SHA-256 cbe659a3289989ba9faef835d6493ac530fbf3957ae2cae412e32b2f4308c4cb)
```

`verify` works on any supported file, including files cleaned by other
programs. With `--original` it also checks that the image, drawing,
document, audio or archive content is the same as the original's. On the
original itself it says what is left:

```
$ metaex verify photo.jpg
metaex verify: photo.jpg
Result: NOT CLEAN

Leftovers (74):
  EXIF: Make = ExampleCam
  EXIF: Model = Model X
  ...
```

### show: every field

`metaex show photo.jpg` lists everything metaex found, section by section,
with `*` marking sensitive fields, plus file system information (dates,
size, owner, the Windows "downloaded from the internet" mark) and hashes.
`--structure` lists the file's segments, chunks or blocks;
`--extract-thumbnail OUT` saves a JPEG's embedded thumbnail so you can
compare it with the photo.

## Many files and folders

`show`, `risk`, `strip` and `verify` accept several files and folders:

```
metaex risk photos --recursive --quiet                 # what a whole folder reveals
metaex strip photos --recursive --output-dir clean     # cleaned copies in another folder
metaex verify clean --original photos --recursive      # check them all
metaex risk photos --recursive --csv > findings.csv    # every finding, for a spreadsheet
metaex strip a.jpg b.png report.docx                   # a_clean.jpg, b_clean.png, ...
```

Real output (on Windows the paths are printed with `\`):

```
$ metaex strip photos --recursive --output-dir clean
[1/6] cleaned: photos/2024/beach.jpg -> clean/2024/beach.jpg
  Warning: ICC profile names a device (ACME X100); use --paranoid to remove it (colors may shift)
[2/6] cleaned: photos/2024/report.docx -> clean/2024/report.docx
  Warning: *** the document still contains tracked changes, and 14 characters of DELETED TEXT are still stored in the file, where anyone can read them. ...
  Warning: kept 1 comment(s) (their authors are now "Author"); use --remove-comments to remove them
  ...
[3/6] cleaned: photos/gps_camera.jpg -> clean/gps_camera.jpg
[4/6] skipped: photos/notes.txt: unsupported format (detected type: unknown)
[5/6] already clean: photos/orientation_6.jpg -> clean/orientation_6.jpg
[6/6] refused: photos/truncated.jpg: missing end-of-image (EOI) marker; the file may be truncated; no file written

Summary: 7 entries
  cleaned        3
  already clean  1
  skipped        2
  refused        1
  error          0

By format:
  DOCX        1 (1 cleaned)
  JPEG        4 (2 cleaned, 1 already clean, 1 refused)
  unknown     1 (1 skipped)
  (not read)  1 (1 skipped)

Refused (1):
  photos/truncated.jpg: missing end-of-image (EOI) marker; the file may be truncated; no file written

Skipped (2):
  photos/.DS_Store: hidden (use --include-hidden to include it)
  photos/notes.txt: unsupported format (detected type: unknown)

Run "metaex formats" to see the supported formats.
```

```
$ metaex risk photos --recursive --quiet
Summary: 7 entries
  examined  5
  skipped   2
  refused   0
  error     0

Findings in the examined files:
  critical  63 (in 4 files)
  high      90 (in 4 files)
  medium    30 (in 4 files)
  low       80 (in 5 files)

Files with critical findings (4):
  photos/2024/beach.jpg: 20 critical
  photos/2024/report.docx: 19 critical
  photos/gps_camera.jpg: 12 critical
  photos/truncated.jpg: 12 critical
...
```

- **Which files.** Files you name are always processed. Folders are
  searched one level deep; `--recursive` (`-r`) includes subfolders. Left
  out, and listed in the summary: hidden files (`--include-hidden` includes
  them), `.git`, `node_modules`, virtual environments and similar folders,
  symbolic links (never followed), metaex's own `*_clean.*` copies, the
  `--output-dir` folder, and files in formats metaex doesn't support.
  Files are processed once each, sorted by path.
- **Where copies go.** Next to each original, or with `--output-dir DIR`
  into `DIR` with the same folder structure (`photos/2024/beach.jpg` ->
  `clean/2024/beach.jpg`). A copy that already exists is skipped
  (`--overwrite` replaces it); metaex never writes over an original.
  `--in-place` asks once for the whole run.
- **One bad file never stops the run.** Each file gets an outcome:
  `cleaned`, `already clean`, `skipped`, `refused` or `error` (`clean` or
  `not clean` for `verify`, `examined` for `show` and `risk`). Ctrl+C stops
  after the current file and prints the summary so far.
- **Checking a folder.** `metaex verify clean --original photos -r` pairs
  each cleaned file with the original at the same place
  (`photo_clean.jpg` also pairs with `photo.jpg`) and lists the files
  without a pair.
- **`--quiet`** (`-q`) prints only the summary, and the files that were
  refused or failed.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | success: the file was examined, cleaned or verified clean |
| 1 | an error, or a file was refused (damaged, too large, not safe to clean; for `strip` and `verify` also an unsupported format), or the run was interrupted |
| 2 | wrong command-line options |
| 3 | `verify` found metadata left, or content that differs from `--original` |

With several files: 1 if any file was refused or failed, else 3 if
`verify` found a file that is not clean, else 0.

## JSON and CSV

`--json` prints the full result as JSON: one object for a single file, or
every file with its outcome plus the summary for several.

`--csv` (for `show`, `risk` and `verify`) prints one row per finding: path,
format, section, field, value and risk level. The file is UTF-8 with a byte
order mark, so Excel shows every character correctly. Metadata comes from
untrusted files, so a cell that starts with `=`, `+`, `-`, `@`, a tab or a
carriage return gets a leading `'`: spreadsheets then show it as text
instead of running it as a formula. With several files the progress lines
and the summary go to stderr, so the output stays valid CSV.

## Limitations

Removing metadata does not make a file anonymous. What metaex does **not**
remove or detect:

- **Visible content.** Faces, street signs, screens and reflections in a
  photo; names and addresses in the text, cells, headers, slides and
  **speaker notes** of a document; **text inside an image**; voices and
  background sounds in a recording. That is the content itself, not
  metadata. So is text hidden in plain sight (white on white, behind an
  image, under a "redaction" box): it stays, and so do hidden text, hidden
  sheets and slides (metaex warns about them).
- **Deleted text in tracked changes.** A document with tracked changes
  still stores the deleted text. metaex warns loudly but does not accept or
  reject the changes for you: do that in Word or LibreOffice, save, and run
  metaex again.
- **Macros and embedded objects** in office documents are kept as they are;
  their code and content can hold names, paths and other details.
- **Data hidden inside the content.** metaex never decodes images or audio,
  so data hidden inside the image or sound data itself (steganography,
  watermarks, camera sensor noise patterns that can link photos to one
  device) is not found. In particular: bytes hidden in a **GIF's LZW
  data**, inside **WebP's VP8/VP8L bitstreams**, in the pixels of a PNG or
  in a JPEG's compressed data, inside MP3 frames, and **between the FLAC
  audio frames** (only the first and last frame are parsed) are not
  detected. Hidden data *around* the content (after the end of an image, in
  padding, between MP3 frames, in a PNG's compressed stream) is detected and
  removed.
- **Traces of the software.** A JPEG keeps its quantization tables and a ZIP
  entry keeps its compressed bytes, which can hint at the camera or program
  that made them.
- **Names of files in archives.** Every file inside a cleaned archive keeps
  its name, and the order of the files is kept. A name can reveal a person,
  a project or a user folder (metaex warns about user folders). Without
  `--deep`, the files inside also keep their metadata; with it, formats
  metaex doesn't read (PDF, plain text, ...) are copied as they are.
- **Formats metaex refuses.** PDF, HEIC, TIFF, video, legacy Office files
  (.doc, .xls, .ppt), password-protected documents and the other formats
  `metaex formats` lists are refused, never cleaned: metaex writes nothing
  and tells you why.
- **Metadata added when you upload.** A website, app or messaging service
  records its own data when you upload a file (your account, IP address,
  upload time) and may add metadata to its copy. metaex can only clean the
  file before it leaves your computer.
- **File system metadata outside the file.** The dates, owner and
  permissions your system keeps about a file, the Windows "downloaded from
  the internet" mark, macOS extended attributes, backups, synced cloud
  copies, thumbnail caches and older versions of the original are not part
  of the file, so metaex can't change them. A cleaned copy is a new file
  with new dates; the original keeps everything until you delete it.

Each format's own limits are listed in [FORMATS.md](https://github.com/jmendoza167/metadata_extractor/blob/main/FORMATS.md).

## More

- [FORMATS.md](https://github.com/jmendoza167/metadata_extractor/blob/main/FORMATS.md):
  what metaex does with each format.
- [SECURITY.md](https://github.com/jmendoza167/metadata_extractor/blob/main/SECURITY.md):
  the threat model and how to report a vulnerability privately.
- [CHANGELOG.md](https://github.com/jmendoza167/metadata_extractor/blob/main/CHANGELOG.md):
  what changed in each version.
- [Issues](https://github.com/jmendoza167/metadata_extractor/issues): bug
  reports and questions. Never attach a personal file; describe the problem
  or build a test file with made-up data.

## License

MIT. See [LICENSE](https://github.com/jmendoza167/metadata_extractor/blob/main/LICENSE).
