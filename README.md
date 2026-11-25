# metaex

**metaex** is a command-line tool that shows and removes metadata from
files: photos, documents, audio and archives. It is written in Python and
uses only the standard library, so there is nothing to install besides
Python itself.

> **Status: work in progress. The supported formats so far are images
> (JPEG, PNG, GIF, WebP, SVG), office documents (Word, Excel and
> PowerPoint: DOCX, XLSX, PPTX; LibreOffice and other OpenDocument
> programs: ODT, ODS, ODP), audio (MP3, WAV, FLAC) and archives (ZIP, TAR,
> gzip, bzip2, xz).** For these, metaex can show, assess, strip and verify
> metadata. For every other format it only shows file system information
> (size, dates, hashes) and refuses to strip.

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

## Supported formats

| Format | show | risk | strip | verify |
|--------|------|------|-------|--------|
| JPEG   | yes  | yes  | yes   | yes    |
| PNG    | yes  | yes  | yes   | yes    |
| GIF    | yes  | yes  | yes   | yes    |
| WebP   | yes  | yes  | yes   | yes    |
| SVG    | yes  | yes  | yes   | yes    |
| DOCX, XLSX, PPTX | yes | yes | yes | yes |
| ODT, ODS, ODP    | yes | yes | yes | yes |
| MP3    | yes  | yes  | yes   | yes    |
| WAV    | yes  | yes  | yes   | yes    |
| FLAC   | yes  | yes  | yes   | yes    |
| ZIP    | yes  | yes  | yes   | yes    |
| TAR    | yes  | yes  | yes   | yes    |
| gzip, bzip2, xz (.gz, .tgz, .tar.gz, .bz2, .tar.bz2, .xz, .tar.xz, ...) | yes | yes | yes | yes |

The Office rows include the macro-enabled and template variants (.docm,
.dotx, .dotm, .xlsm, .xltx, .xltm, .pptm, .potx, .potm, .ppsx, .ppsm; .ott,
.ots, .otp). See [Office documents](#office-documents) for what metaex
reads, removes and keeps, and [Audio files](#audio-files) for MP3, WAV and
FLAC, and [Archives](#archives) for ZIP, TAR and compressed files.

For JPEG, metaex reads EXIF (including GPS and the embedded thumbnail), XMP
and Extended XMP, IPTC and Photoshop resources, ICC profiles, MPF extra
images, JFIF/JFXX, comments, unknown APP segments and data after the end of
the image.

For PNG, metaex reads text chunks (tEXt, zTXt, iTXt, including unknown
keywords), AI generation data (Stable Diffusion "parameters", ComfyUI
"prompt"/"workflow", InvokeAI), XMP, EXIF (eXIf, including GPS), the
modification time (tIME), the ICC profile (iCCP), color and display chunks
(sRGB, gAMA, cHRM, cICP, mDCv, cLLI, pHYs, bKGD, sBIT, hIST, sPLT, oFFs,
pCAL, sCAL), Content Credentials (caBX, reported but not parsed), APNG
animation chunks, unknown chunks and data after the end of the image. Every
chunk's CRC is checked. Compressed text and profiles are decompressed with
a 16 MiB limit per file, so a "decompression bomb" can't exhaust memory.
The image data itself (IDAT, and each APNG frame) is decompressed piece by
piece and compared with the exact size the image needs, which finds data
hidden inside the compressed image stream.

For GIF, metaex reads comments, Plain Text extensions, XMP (as the XMP
specification stores it, with its "magic trailer", or in ordinary
sub-blocks), the ICC profile (ICCRGBG1012), the animation loop count
(NETSCAPE2.0 / ANIMEXTS1.0) and frame count, any other application or
unknown extension (listed by identifier) and data after the trailer.

For WebP, metaex reads EXIF (including GPS; with or without an
"Exif\0\0" prefix), XMP, the ICC profile (ICCP), animation (ANIM and the
ANMF frames, including chunks inside frames), unknown chunks, the VP8X
flags (and whether they match the chunks present) and data after the end
of the RIFF data.

For SVG, metaex reads `<metadata>` (RDF / Dublin Core: creator, date,
rights, publisher, ...), `<title>` and `<desc>`, XML comments, processing
instructions, editor data (Inkscape/Sodipodi, Adobe Illustrator, Sketch,
Affinity, Visio, Krita namespaces: document names, export file names,
program versions, window sizes, layer names), **absolute local file
paths** in any attribute (`C:\Users\<name>\...`, `/home/<name>/...`,
`/Users/<name>/...`, `file:///...`), and **embedded images** (data: URIs),
which are decoded and read with their own format's parser, so a JPEG's GPS
inside an SVG is found too. It also warns about `<script>` elements, event
handler attributes and external `http(s)` references (opening the SVG can
contact that server). The standard SVG 1.0 and 1.1 DOCTYPE line found in
older files is accepted: metaex never downloads the DTD it points to, lists
the line as low-risk information and removes it when stripping (it doesn't
change the drawing). Any other DOCTYPE, ENTITY declarations, entities that
only a DTD defines (such as `&nbsp;`) and files that are not UTF-8 are
refused before parsing.

### Refused

**Refused, with an explanation:** legacy Office files (.doc, .xls, .ppt:
save them in the current format first), password-protected Office and
OpenDocument files (remove the password first), "Strict Open XML" documents
and binary Excel workbooks (.xlsb); audio in other formats: AIFF, Ogg
(Vorbis, Opus), M4A/AAC (also AAC in a file named .mp3), WAV files over
4 GiB (RF64, BW64) and "free-format" MP3 (a rare variant without a fixed
frame size); archives in other formats (7-Zip, RAR), EPUB e-books, Java
and Android packages (JAR, WAR, EAR, APK) and every signed ZIP archive
(cleaning it would break the signature), ZIP archives with encrypted
entries, and TAR archives with sparse files.

`metaex show` recognizes these (and others) by their content, but does not
read their embedded metadata; `metaex strip` refuses them and writes
nothing. Every command says which type it detected and that metaex doesn't
support it yet; `metaex formats` lists the supported and refused formats.

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

## Usage

```
metaex show photo.jpg              # list all metadata, with sensitive fields marked
metaex risk photo.jpg              # what the metadata reveals, by risk level
metaex strip photo.jpg             # write photo_clean.jpg without metadata
metaex verify photo_clean.jpg --original photo.jpg   # check the result
metaex formats                     # the supported formats and what strip removes
```

All four commands also work on several files and whole folders; see
[Many files and folders](#many-files-and-folders).

PNG, GIF, WebP, SVG, office documents, audio files and archives work the
same way (`metaex strip report.docx` writes `report_clean.docx`, `metaex
strip backup.tar.gz` writes `backup_clean.tar.gz`). The examples below are real output for a JPEG
generated by the test suite (all values are made up); examples for the
other formats follow in [PNG example](#png-example),
[GIF example](#gif-example), [WebP example](#webp-example),
[SVG example](#svg-example) and [Office documents](#office-documents).

To try metaex without using your own files, run it on the sample files in
[tests/samples/](tests/samples/SAMPLES.md) (all their metadata is fake), e.g.
`metaex risk tests/samples/jpeg/gps_camera.jpg`.

### risk

```
$ metaex risk photo.jpg
metaex risk: photo.jpg
12 critical, 14 high, 5 medium, 7 low

Notes:
  ! embedded thumbnail may show the original, uncropped image

CRITICAL (12)
  EXIF: BodySerialNumber                    SN-0001
  GPS: Coordinates                          48.858367, 2.294481
  GPS: Latitude                             48.858367
  GPS: Longitude                            2.294481
  ...                                       (output shortened here)
  Thumbnail: Embedded thumbnail             JPEG, 123 bytes
  XMP: exif:GPSLatitude                     48,51.502N
  XMP: exif:GPSLongitude                    2,17.669E

HIGH (14)
  EXIF: Make                                ExampleCam
  EXIF: Model                               Model X
  EXIF: DateTimeOriginal                    2024-05-01T14:30:00+02:00
  XMP: xmpMM:DocumentID                     xmp.did:1234
  XMP: dc:creator                           Jane Doe
  ...                                       (output shortened here)

MEDIUM (5)
  EXIF: ExposureTime                        1/250 s
  EXIF: FNumber                             f/1.8
  ...                                       (output shortened here)

LOW (7)
  ...                                       (output shortened here)

Run "metaex strip photo.jpg" to remove these.
```

Levels: **critical** (GPS, thumbnails, hidden extra images, data after the
end of the image or hidden inside the image data, serial numbers, owner
name, local file paths in SVG), **high** (make/model, dates, authors,
software, edit history and document IDs, download origin, comments, PNG
text chunks, AI prompts and settings, Content Credentials, GIF plain text
and unknown application extensions, SVG metadata, processing instructions
and editor data), **medium** (camera settings, ICC device info and profile
names, unknown segments or chunks, SVG `<title>`/`<desc>`), **low**
(everything else, including zero padding after the end of the image). A
property found in both standard and Extended XMP is counted once. For
office documents: **critical** (author and last editor, account user IDs,
file paths that name a user, deleted text kept by tracked changes, unused
parts and data hidden in the ZIP container, the authors of embedded
documents), **high** (company, manager, template, dates, application and
version, comment and revision authors, sensitivity labels, custom
properties and data, document variables, links and data connections,
macros, embedded objects, hidden text), **medium** (editing time,
revision number, printer settings, revision IDs, thumbnail, hidden sheets
and slides, speaker notes), **low** (counts and everything else). For
audio files: **critical** (e-mail addresses in any tag, account and
purchase identifiers: ID3 POPM, PRIV, UFID, OWNE; data hidden in the
audio stream or in tag padding; GPS in cover art), **high** (artist,
composer, originator, encoded by, copyright, dates, comments, lyrics, user
links, recording devices and places, original file names, embedded
pictures, cue point labels), **medium** (titles and albums, encoder
software and settings, Broadcast WAV coding history, iXML and axml project
data, unknown fields, chunks and blocks), **low** (format, bitrate, sample
rate, channels, the LAME tag, track numbers, genre, cue points and loops).
On a terminal the levels are colored; use `--no-color`
or set `NO_COLOR` to turn that off. `--json` prints the findings as JSON.

### strip

```
$ metaex strip photo.jpg
metaex strip: photo.jpg
Wrote photo_clean.jpg (962 B; original 2.9 KiB)

Removed:
  Camera and owner                3 fields: Make, Model, BodySerialNumber
  Camera settings and other EXIF  6 fields: Orientation, ExposureTime, FNumber, ISOSpeedRatings, ...
  Software                        1 field: Software
  Dates                           3 fields: DateTime, DateTimeOriginal, OffsetTimeOriginal
  GPS location                    8 fields: Coordinates, Latitude, Longitude, OpenStreetMap, ...
  Thumbnail                       3 fields: Embedded thumbnail, Dimensions, Compression
  XMP                             13 fields: XMP packet, xmp:CreatorTool, xmpMM:DocumentID, xmpMM:OriginalDocumentID, ...

Kept by design:
  (nothing)

Verification: CLEAN
  Image data unchanged (SHA-256 cbe659a3289989ba9faef835d6493ac530fbf3957ae2cae412e32b2f4308c4cb)

File timestamps: new (not copied from the original)
Windows Zone.Identifier stream: none (no download origin attached)
```

How it works, and what is guaranteed:

- **Whitelist.** Only what is needed to display the image is kept, copied
  **byte-for-byte**. The image is never re-encoded, so there is no quality
  loss.
  - JPEG: the frame header, quantization and Huffman tables, restart
    interval, scan headers and scan data. Dropped: EXIF, XMP,
    IPTC/Photoshop, MPF, comments, unknown segments and any data after the
    end of the image.
  - PNG: IHDR, PLTE, tRNS, every IDAT, IEND and the APNG animation chunks
    (acTL, fcTL, fdAT), in their original order. Dropped: all text chunks
    (including AI generation data and XMP), tIME, eXIf, caBX, bKGD, sPLT,
    hIST, sBIT, oFFs, pCAL, sCAL, unknown chunks and any data after IEND.
  - GIF: the header, screen descriptor, color tables, every image
    (descriptor, local color table, LZW data) and the trailer. The Graphic
    Control extension of each image (timing, transparency) and the loop
    count are rewritten in their minimal form. Dropped: comments, Plain
    Text extensions, XMP, all other application and unknown extensions and
    any data after the trailer.
  - WebP: VP8, VP8L, ALPH, ANIM and the ANMF frames (keeping only their
    image sub-chunks). A new VP8X chunk is written whose flags match exactly
    the chunks kept, the RIFF size is recomputed and padding bytes are
    zero. Dropped: EXIF, XMP, unknown chunks (also inside frames) and any
    data after the RIFF data.
  - **SVG is the exception**: it is text, so the cleaned copy is
    **re-serialized** (parsed and written again) rather than copied
    byte-for-byte. Everything that is kept comes out as parsed, and the
    namespace prefixes stay exactly as written (the default SVG namespace,
    `xlink:`; never `ns0:`), but formatting inside tags can change
    (quoting, `<a></a>` written as `<a/>`, the indentation of removed
    elements). Dropped: the standard DOCTYPE line, `<metadata>`, comments,
    processing instructions (the XML declaration stays), every element and
    attribute in an editor namespace (`sodipodi:namedview`, `inkscape:*`,
    Illustrator `i:*`, ...)
    and the declarations of namespaces no longer used. Embedded images
    (data: URIs) are stripped with their own format's rules and written
    back as base64.
- **Kept by design**, because removing them would change how the image
  looks:
  - the EXIF **Orientation** (only if the image is rotated or mirrored;
    written as a new, minimal EXIF with that single tag);
  - the **ICC color profile**. In PNG its name is replaced with the
    generic "ICC profile". If the profile names a device (manufacturer or
    model), the summary warns about it;
  - JPEG: a new minimal **JFIF** header (version and density only) and the
    **Adobe** segment needed for CMYK images;
  - PNG: the color chunks **sRGB, gAMA, cHRM, cICP, mDCv, cLLI** and the
    pixel density **pHYs** (DPI), each only if it has its exact expected
    size;
  - GIF and WebP: the animation (loop count, frames);
  - SVG: `<title>` and `<desc>`, because screen readers use them; the
    summary shows what they say.

  `--paranoid` also removes the ICC profile, the JFIF density (JPEG),
  gAMA, cHRM, cICP, mDCv, cLLI and pHYs (PNG; sRGB stays) and SVG
  `<title>`/`<desc>`, so colors may shift. `--no-orientation` drops the
  orientation (the image may display rotated). For audio files,
  `--paranoid` removes the MP3 Xing/LAME frame, WAV cue points and loops
  and the FLAC cue sheet; see [Audio files](#audio-files).
- **Hidden data in a PNG's image stream is refused**, not copied: extra
  bytes after the pixels or after the end of the compressed stream make
  `strip` stop and explain. `--rebuild-image-data` then decompresses
  exactly the bytes the image needs and compresses them again (zlib level
  9) into new IDAT chunks. PNG compression is lossless, so the pixels are
  identical, and `verify` says "image data rebuilt, pixels identical"
  (it compares the SHA-256 of the decompressed data). Hidden data in APNG
  frames is always refused.
- **SVG files that link to local files are refused**: an `<image>` that
  points to `C:\Users\<name>\...` reveals the user name, but removing the
  path would break the drawing, so metaex lists the paths and writes
  nothing. Paths in editor attributes (`inkscape:export-filename`,
  `sodipodi:absref`) are simply removed. Embedded data that can't be
  stripped (an unsupported or damaged image, data: URIs inside CSS) is
  refused the same way.
- **Verified before it exists.** The cleaned copy is written to a temp file
  in the destination folder, read back, re-parsed from scratch and checked
  for leftovers and unchanged image data. Only then is it moved into place.
  If anything fails, nothing is written.
- **Never overwrites.** The default output is `<name>_clean.<ext>`; if that
  exists, metaex refuses (use `-o` to choose a name, or `--overwrite` to
  replace it; the original itself is never replaced that way). `--in-place`
  replaces the original, after typing `yes` (or with `--yes`).
- **Malformed files are refused**, never partly cleaned. For PNG that
  includes any chunk with a bad CRC, a missing IEND, critical chunks metaex
  doesn't know and image data that is too short or invalid; for GIF a
  missing trailer; for WebP a RIFF size larger than the file and images
  with more or fewer than one bitstream; for SVG anything that is not
  well-formed XML.

Other options: `-o OUTPUT` (one file), `--output-dir DIR` (write the
cleaned copy into `DIR` under its original name), `--overwrite`, `--rename` (random name like
`img_8f3a2c1d.jpg`), `--fixed-time` (sets the file's modified/accessed time
to 2000-01-01T00:00:00Z; the creation time is set by the operating system),
`--remove-comments` (office documents: remove comments completely instead
of keeping them with "Author" as their author), `--deep` (archives: clean
the files inside too; see [Archives](#archives)), `--json`.

### verify

```
$ metaex verify photo_clean.jpg --original photo.jpg
metaex verify: photo_clean.jpg
Result: CLEAN

Kept by design:
  (nothing)

Image data: matches the original (SHA-256 cbe659a3289989ba9faef835d6493ac530fbf3957ae2cae412e32b2f4308c4cb)
```

`verify` works on any supported file, including files cleaned by other
tools. Exit codes: **0** clean, **3** not clean (leftovers found, or the
image data differs from `--original`), **1** error.

"Image data" means what defines the picture: for JPEG the frame, tables and
scan data; for PNG IHDR, PLTE, tRNS, the joined IDAT data and the APNG
frame chunks; for GIF the screen descriptor, color tables, Graphic Control
fields, image descriptors, LZW data and loop count; for WebP the canvas
size, the image chunks and the frames. For SVG, verify compares the
**drawing** instead: a canonical form of every element, attribute and text
that is drawn (by namespace, not prefix, and ignoring what strip removes),
with each embedded image replaced by the hash of its own image data. For
office documents it compares the **document content**: every file in the
document, byte for byte, except where strip is allowed to change something
(authors, dates, revision IDs, file paths, comment marks when comments are
removed); those files are compared in a form that leaves out exactly those
changes, and images are compared by their image data.

### show

`metaex show photo.jpg` lists everything metaex found, section by section,
with `*` marking sensitive fields. Useful options: `--json`, `--structure`
(list every JPEG segment, PNG or WebP chunk, GIF block or the SVG root and
namespaces), `--extract-thumbnail OUT` (JPEG only: save the embedded
thumbnail to compare it with the main image).

### PNG example

Real output for a PNG generated by the test suite with every kind of
metadata metaex reads (all values are made up):

```
$ metaex risk image.png
metaex risk: image.png
11 critical, 30 high, 6 medium, 34 low

CRITICAL (11)
  XMP: exif:GPSLatitude                     48,51.502N
  EXIF: BodySerialNumber                    SN-0001
  GPS: Coordinates                          48.858367, 2.294481
  ...                                       (output shortened here)
  Trailing data: Size                       17 bytes

HIGH (30)
  Text: Author                              Jane Doe
  Text: Private Note                        meet at the cafe at 5
  AI generation data: parameters            a photo of a cat on a beach\nNegative prompt: blurry\nSteps: 20, Sampler: Eul...
  AI generation data: prompt                {"3": {"class_type": "KSampler", "inputs": {"seed": 1234567}}}
  Time: Last modified                       2024-05-01T12:30:00Z
  Content Credentials: C2PA manifest        32 bytes at offset 3218
  ...                                       (output shortened here)

MEDIUM (6)
  ICC profile: Profile name                 Jane's MacBook display
  ICC profile: Device manufacturer          ACME
  Unknown chunks: prVt                      12 bytes at offset 3262
  ...                                       (output shortened here)

LOW (34)
  ...                                       (output shortened here)

Run "metaex strip image.png" to remove these.
```

```
$ metaex strip image.png
metaex strip: image.png
Wrote image_clean.png (727 B; original 3.2 KiB)

Removed:
  Text                        12 fields: Author, Software, Title, Description, ...
  AI generation data          3 fields: parameters, prompt, workflow
  XMP                         13 fields: XMP packet, xmp:CreatorTool, xmpMM:DocumentID, xmpMM:OriginalDocumentID, ...
  Camera and owner            3 fields: Make, Model, BodySerialNumber
  Software                    1 field: Software
  Dates                       2 fields: DateTime, DateTimeOriginal
  GPS location                5 fields: Coordinates, Latitude, Longitude, OpenStreetMap, ...
  Modification time           1 field: Last modified
  ICC profile                 1 field: Profile name "Jane's MacBook display" (replaced with "ICC profile")
  Color and display           7 fields: Significant bits, Background color, Palette histogram, Suggested palette, ...
  Content Credentials (C2PA)  1 field: C2PA manifest
  Unknown chunks              1 field: prVt
  Data after end of image     17 B

Kept by design:
  EXIF: Orientation = Rotated 90 degrees clockwise
  ICC profile: kept so colors stay correct (description: "Test RGB profile")
  Color and display: kept so the image displays correctly: sRGB, Gamma, Chromaticities, Color coding (cICP), Mastering display, Content light level, Pixel density
  Animation: Frames = 2, Plays = forever, Frame control chunks (fcTL) = 2, Frame data chunks (fdAT) = 1
  Warning: ICC profile names a device (ACME X100); use --paranoid to remove it (colors may shift)

Verification: CLEAN
  Image data unchanged (SHA-256 03ce548db8058ab8ddd887827310346e5f71b681d26b694b65a108965051fa82)

File timestamps: new (not copied from the original)
Windows Zone.Identifier stream: none (no download origin attached)
```

```
$ metaex verify image_clean.png --original image.png
metaex verify: image_clean.png
Result: CLEAN

Kept by design:
  EXIF: Byte order = big-endian (MM)
  EXIF: Orientation = Rotated 90 degrees clockwise
  ICC profile: Profile name = ICC profile
  ...                                       (output shortened here)

Image data: matches the original (SHA-256 03ce548db8058ab8ddd887827310346e5f71b681d26b694b65a108965051fa82)
```

### GIF example

Real output for a GIF generated by the test suite with every kind of
metadata metaex reads (all values are made up):

```
$ metaex risk animation.gif
metaex risk: animation.gif
5 critical, 14 high, 2 medium, 18 low

CRITICAL (5)
  XMP: exif:GPSLatitude                     48,51.502N
  XMP: exif:GPSLongitude                    2,17.669E
  Trailing data: Size                       20 bytes
  ...                                       (output shortened here)

HIGH (14)
  Comments: Comment                         Created by Jane Doe with Editor 2.0
  Plain text: Plain text                    meet at the cafe at 5
  XMP: xmp:CreatorTool                      Editor 2.0
  Application extensions: MGK8BIM0000       37 bytes at offset 1845
  Application extensions: ImageMagick       24 bytes at offset 2107
  ...                                       (output shortened here)

MEDIUM (2)
  ICC profile: Device manufacturer          ACME
  ICC profile: Device model                 X100

LOW (18)
  ...                                       (output shortened here)

Run "metaex strip animation.gif" to remove these.
```

```
$ metaex strip animation.gif
metaex strip: animation.gif
Wrote animation_clean.gif (440 B; original 2.1 KiB)

Removed:
  Comments                 2 fields: Comment, Comment
  Plain text               1 field: Plain text
  XMP                      13 fields: XMP packet, xmp:CreatorTool, xmpMM:DocumentID, xmpMM:OriginalDocumentID, ...
  Application extensions   3 fields: MGK8BIM0000, Extension 0x42, ImageMagick
  Data after end of image  20 B

Kept by design:
  ICC profile: kept so colors stay correct (description: "Test RGB profile")
  Animation: Frames = 3, Loop count = forever
  Warning: ICC profile names a device (ACME X100); use --paranoid to remove it (colors may shift)

Verification: CLEAN
  Image data unchanged (SHA-256 2601bdae84462f8012750454a3e29275031a72d38078c27315a5e4dbfd8a2bc3)

File timestamps: new (not copied from the original)
Windows Zone.Identifier stream: none (no download origin attached)
```

### WebP example

```
$ metaex strip image.webp
metaex strip: image.webp
Wrote image_clean.webp (428 B; original 2.0 KiB)

Removed:
  Camera and owner         3 fields: Make, Model, BodySerialNumber
  Software                 1 field: Software
  Dates                    2 fields: DateTime, DateTimeOriginal
  GPS location             5 fields: Coordinates, Latitude, Longitude, OpenStreetMap, ...
  XMP                      13 fields: XMP packet, xmp:CreatorTool, xmpMM:DocumentID, xmpMM:OriginalDocumentID, ...
  Unknown chunks           3 fields: FRMx, prVt, odd!
  Data after end of image  17 B

Kept by design:
  EXIF: Orientation = Rotated 90 degrees clockwise
  ICC profile: kept so colors stay correct (description: "Test RGB profile")
  Animation: Frames = 2, Loop count = forever, Background color = #000000, alpha 255
  Warning: ICC profile names a device (ACME X100); use --paranoid to remove it (colors may shift)

Verification: CLEAN
  Image data unchanged (SHA-256 532c1a272cc97bcbfb0027ff61169adbb37e8ff83ebaa11b7d22ddb4316056e1)

File timestamps: new (not copied from the original)
Windows Zone.Identifier stream: none (no download origin attached)
```

(`metaex risk image.webp` for the same file: 11 critical, 13 high, 5
medium, 21 low; the critical findings are the serial number, the GPS
position in EXIF and XMP, and the data after the end of the RIFF data.)

### SVG example

An Inkscape-style SVG generated by the test suite, with an embedded photo
(all values are made up):

```
$ metaex risk plan.svg
metaex risk: plan.svg
14 critical, 48 high, 7 medium, 10 low

Notes:
  ! Embedded image 1 (JPEG): embedded thumbnail may show the original, uncropped image
  ! the SVG contains 1 <script> element(s) (first at line 106); they can run code when the file is opened in a browser
  ! external reference: https://tracker.example/pixel.png (href of <image> at line 107); opening the SVG can contact that server, which can be used for tracking

CRITICAL (14)
  Local file paths: inkscape:export-filename on <svg>  /home/jane/Pictures/floor plan.png
  Local file paths: sodipodi:absref on <image>  /home/jane/Pictures/photo.jpg
  Embedded image 1 (JPEG): EXIF: BodySerialNumber  SN-0001
  Embedded image 1 (JPEG): GPS: Coordinates  48.858367, 2.294481
  ...                                       (output shortened here)

HIGH (48)
  Metadata: dc:creator/cc:Agent/dc:title    Jane Doe
  Metadata: dc:date                         2024-05-01
  Comments: Comment                         TODO ask jane about the stairs
  Editor data: sodipodi:docname             floor plan.svg
  Editor data: inkscape:version             1.2.2 (b0a8486541, 2022-12-01)
  Embedded image 2 (PNG): Text: Author      Jane Doe
  ...                                       (output shortened here)

MEDIUM (7)
  Title and description: Title              Floor plan for jane
  Title and description: Description        Draft for the landlord, do not share
  ...                                       (output shortened here)

LOW (10)
  ...                                       (output shortened here)

Run "metaex strip plan.svg" to remove these.
```

```
$ metaex strip plan.svg
metaex strip: plan.svg
Wrote plan_clean.svg (2.6 KiB; original 7.4 KiB)

Removed:
  Metadata                        7 fields: metadata element, dc:format, dc:title, dc:creator/cc:Agent/dc:title, ...
  Comments                        3 fields: Comment, Comment, Comment
  Processing instructions         1 field: xml-stylesheet
  Editor data                     21 fields: Inkscape namespace, Sodipodi (Inkscape) namespace, <sodipodi:namedview>, <inkscape:path-effect>, ...
  Local file paths                2 fields: inkscape:export-filename on <svg>, sodipodi:absref on <image>
  Embedded images                 3 fields: Embedded image 1 "JPEG, 2920 bytes" (replaced with "JPEG, 962 bytes"), Embedded image 2 "PNG, 182 bytes" (replaced with "PNG, 117 bytes"), Embedded image 3 "SVG, 157 bytes" (replaced with "SVG, 128 bytes")
  Camera and owner                3 fields: Make, Model, BodySerialNumber
  ...                                       (output shortened here)
  GPS location                    8 fields: Coordinates, Latitude, Longitude, OpenStreetMap, ...
  Text                            2 fields: Author, Comment

Kept by design:
  Title and description: Title = Floor plan for jane, Description = Draft for the landlord, do not share
  Embedded images: Embedded image 1 = JPEG, 962 bytes, Embedded image 2 = PNG, 117 bytes, Embedded image 3 = SVG, 128 bytes
  Warning: kept 2 <title>/<desc> element(s) for accessibility (e.g. 'Floor plan for jane'); use --paranoid to remove them
  Warning: the SVG contains 1 <script> element(s) (first at line 16); they can run code when the file is opened in a browser
  Warning: external reference: https://tracker.example/pixel.png (href of <image> at line 17); opening the SVG can contact that server, which can be used for tracking

Verification: CLEAN
  Drawing unchanged (SHA-256 94199fe11aa26adc8bf224c540050cc88702f8a8cc30ba464a5912a26b997ab9)

File timestamps: new (not copied from the original)
Windows Zone.Identifier stream: none (no download origin attached)
```

```
$ metaex verify plan_clean.svg --original plan.svg
metaex verify: plan_clean.svg
Result: CLEAN

Kept by design:
  Title and description: Title = Floor plan for jane
  Title and description: Description = Draft for the landlord, do not share
  Embedded images: Embedded image 1 = JPEG, 962 bytes
  Embedded images: Embedded image 2 = PNG, 117 bytes
  Embedded images: Embedded image 3 = SVG, 128 bytes

Drawing: matches the original (SHA-256 94199fe11aa26adc8bf224c540050cc88702f8a8cc30ba464a5912a26b997ab9)
Note: the SVG contains 1 <script> element(s) (first at line 16); they can run code when the file is opened in a browser
Note: external reference: https://tracker.example/pixel.png (href of <image> at line 17); opening the SVG can contact that server, which can be used for tracking
```

## Office documents

metaex reads and cleans Word, Excel and PowerPoint files (DOCX, XLSX,
PPTX) and OpenDocument files from LibreOffice and other programs (ODT,
ODS, ODP).

**What metaex finds**

- **Document properties**: author, last editor, creation, modification and
  print dates, revision number, total editing time, title, subject,
  keywords, description, company, manager, template (often a path such as
  `C:\Users\<name>\...\Normal.dotm`), application and version, who printed it.
- **Custom properties**, **sensitivity labels** (Microsoft Purview, with
  your organization's tenant ID), **SharePoint data** (who the document was
  shared with, document IDs) and **document variables**.
- **Comments and their authors**, including the account they were signed
  in with (Microsoft 365 user IDs, which often are e-mail addresses).
- **Tracked changes**: who changed what and when, and **deleted text that
  is still stored in the file**.
- **Revision IDs** (rsids), which can link documents edited on the same
  computer.
- **File paths and links**: the attached template, linked files and
  images, linked workbooks, data connections, the folder an Excel workbook
  was saved in. Paths that name a user (`C:\Users\<name>`,
  `/home/<name>`) and server names are pointed out.
- **Hidden content**: hidden text, hidden sheets and slides, speaker notes,
  the glossary (building blocks).
- **Embedded files**: images (with their own metadata, such as a photo's
  GPS position), embedded objects (and the authors of embedded Office
  files), macros, printer settings (the printer's name), the thumbnail.
- **The ZIP container itself**: archive comments, timestamps and computer
  details of each file inside, and data hidden outside the files.

**What strip removes**

- All document properties, custom properties, sensitivity labels,
  SharePoint data and document variables (core.xml and app.xml stay, empty;
  OpenDocument: meta.xml stays, empty).
- Revision IDs, printer settings, the thumbnail, the attached template, the
  folder a workbook was saved in, Excel's version and co-authoring IDs,
  user interface settings (OpenDocument), digital signatures (they no
  longer match once the file is cleaned) and files nothing in the document
  uses.
- **Comment and revision authors become "Author"**, the same name for
  everyone, and their initials and dates are removed. The comments and
  changes themselves stay. Where a format requires a value (PowerPoint
  comment author initials and account details, OpenDocument change dates,
  PowerPoint comment dates) an empty or neutral value is written instead:
  an empty string, "Author", "None", or a date from 2000-01-01T00:00:00 on,
  one second apart, in the original order (PowerPoint and LibreOffice sort
  comments and changes by date).
  `--remove-comments` removes comments completely, with their marks in the
  text.
- Every embedded JPEG, PNG, GIF, WebP and SVG image is cleaned with the
  same rules as a separate image file (`--paranoid` and `--no-orientation`
  apply to them).
- The ZIP container is written again with no comments, no extra fields
  and the same fixed time (1980-01-01) for every file, as Microsoft Office
  does.

**How it works**

- Files that don't need cleaning are copied exactly as they were, still
  compressed. In the files that are changed, only the bytes that hold
  metadata change: every namespace prefix and declaration stays as
  written, so Word, Excel and PowerPoint open the result normally.
- Any DOCTYPE or entity declaration in a document is refused (they enable
  XML attacks). So are damaged archives, "zip bombs" (files that would
  expand to an enormous size), file names that point outside the folder
  (`../`) and encrypted entries.

**What strip keeps, with a warning**

- **Tracked changes.** metaex does not accept or reject changes for you,
  so deleted text stays in the file. The summary shows a loud warning:
  accept or reject all changes in Word (Review > Accept > Accept All
  Changes) or LibreOffice (Edit > Track Changes > Accept All), save, and
  run metaex again.
- **Macros** (VBA in .docm/.xlsm/.pptm, Basic and scripts in OpenDocument)
  are kept unchanged; their code can contain names, paths and other
  details.
- **Hidden content** (hidden text, sheets and slides, speaker notes, the
  glossary), **comments** (with "Author" as their author), **links**,
  **data connections**, **embedded objects** and images metaex can't clean
  (EMF, WMF, TIFF, ...). They are part of the document, so metaex reports
  them instead of removing them.

**What strip refuses** (nothing is written): documents that link to files
in a user's folder (removing the link would change the document; change or
remove the link first), embedded images that can't be cleaned, and the
files listed under [Planned](#planned-not-implemented-yet).

Real output for a Word document generated by the test suite (all values
are made up; the same file is `tests/samples/docx/kitchen_sink.docx`):

```
$ metaex risk letter.docx
metaex risk: letter.docx
19 critical, 45 high, 12 medium, 34 low

CRITICAL (19)
  Document properties: Author               Jane Doe
  Document properties: Last modified by     Jane Doe
  Application properties: Template          C:\Users\jane.doe\AppData\Roaming\Microsoft\Templates\Normal.dotm
  People: Person 1: user ID                 S::jane.doe@example.com::aaaaaaaa-1111-2222-3333-444444444444
  Tracked changes: Deleted text still in the file (characters)  14
  Tracked changes: Deleted text             "twelve hundred"
  Local file paths: Attached template (word/settings.xml)  file:///C:\Users\jane.doe\Documents\Custom Office Templates\Letter.dotm
  word/media/image1.jpeg (JPEG): GPS: Coordinates  48.858367, 2.294481
  ...                                       (output shortened here)

HIGH (45)
  Document properties: Created              2024-05-01T10:00:00Z
  Application properties: Company           Example Corp
  Sensitivity labels: MSIP_Label_..._Name   Confidential
  Comments: Comment authors                 Jane Doe
  Tracked changes: Revision authors         Jane Doe, John Example
  ...                                       (output shortened here)

Run "metaex strip letter.docx" to remove these.
```

```
$ metaex strip letter.docx
metaex strip: letter.docx
Wrote letter_clean.docx (9.3 KiB; original 20.9 KiB)

*** WARNING ***
the document still contains tracked changes, and 14 characters of DELETED TEXT are still stored in the file, where anyone can read them. Accept or reject all changes in Word (Review > Accept > Accept All Changes) or LibreOffice (Edit > Track Changes > Accept All), save, and run metaex again.

Removed:
  Document properties             15 fields: Title, Subject, Author, Keywords, ...
  Application properties          20 fields: Template, Total editing time (minutes), Pages, Words, ...
  Custom properties               2 fields: ProjectCode, ContentTypeId
  Comment authors and dates       4 fields: Comment authors "Jane Doe" (replaced with "Author"), Comment author initials, Comment dates, Comment dates (UTC)
  People                          4 fields: Person 1 "Jane Doe" (replaced with "Author"), Person 1: user ID, Person 1: account provider, Person 2
  Revision authors and dates      2 fields: Revision authors "Jane Doe, John Example" (replaced with "Author"), Revision dates
  Revision IDs                    2 fields: Editing sessions listed in settings.xml, Revision ID attributes
  Local file paths                1 field: Attached template (word/settings.xml)
  GPS location                    8 fields: Coordinates, Latitude, Longitude, OpenStreetMap, ...
  Printer settings                1 field: word/printerSettings/printerSettings1.bin
  ...                                       (output shortened here)

Kept by design:
  Comments: Comments = 1, Comment authors = Author
  People: Person 1 = Author
  Tracked changes: Insertions = 1, Deletions = 1, Formatting changes = 1, Revision authors = Author, Deleted text still in the file (characters) = 14, Deleted text = "twelve hundred"
  Hidden content: Hidden text (runs) = 1, Hidden text = Secret note to self, Glossary document (building blocks) = word/glossary/document.xml, 1 paragraphs: Old letterhead of Jane Doe
  External links: Hyperlink (word/document.xml) = https://example.com/flat
  Embedded images: word/media/image1.jpeg = JPEG, 962 bytes
  Warning: kept 1 comment(s) (their authors are now "Author"); use --remove-comments to remove them
  Warning: kept 1 run(s) of hidden text (e.g. 'Secret note to self'): it is part of the document; Word shows it with Home > Show/Hide
  Warning: kept the glossary document (building blocks and AutoText), which can hold text copied from other documents
  Warning: kept 1 external link(s) (e.g. https://example.com/flat); they are part of the document, and opening it may contact those addresses

Verification: CLEAN
  Document content unchanged (SHA-256 c8445705967bf84f23ebff4e32cda57f3bcddbe09bd70cb7f6d12bf1adc0e348)

File timestamps: new (not copied from the original)
Windows Zone.Identifier stream: none (no download origin attached)
```

```
$ metaex verify letter_clean.docx --original letter.docx
metaex verify: letter_clean.docx
Result: CLEAN
...                                       (output shortened here)
Document content: matches the original (SHA-256 c8445705967bf84f23ebff4e32cda57f3bcddbe09bd70cb7f6d12bf1adc0e348)
Note: CRITICAL: the document still contains tracked changes, and 14 characters of DELETED TEXT are still stored in the file, ...
```

OpenDocument files (`metaex strip notes.odt`) work the same way.

## Audio files

metaex reads and cleans MP3, WAV and FLAC files. Every tag is removed,
cover art included; the audio itself stays **byte for byte** the same (it
is never decoded or encoded again), and `verify` proves it.

**What metaex finds**

- **MP3**: ID3v2 tags (versions 2.2, 2.3 and 2.4, at the start of the file
  or appended at the end, several of them if there are), ID3v1 and ID3v1.1
  (with the extended "TAG+" block), APEv1 and APEv2 tags, Lyrics3 tags, and
  the Xing/Info (with the LAME tag) or VBRI frame encoders write before the
  audio. Every ID3 frame is shown: titles, people, dates, comments, lyrics,
  user-defined text and links, pictures and embedded files, and the frames
  that identify you or a purchase: **POPM** (a rating stored with your
  e-mail address), **PRIV** (private data of a program or store), **UFID**
  (an identifier in a music database or store) and **OWNE** (price, date
  and seller). Compressed and unsynchronised frames are read; encrypted
  ones are listed (their content can't be read).
- **WAV**: the RIFF INFO list (artist, comments, dates, software, ...), the
  Broadcast WAV `bext` chunk (description, originator, often the recorder's
  name, origination date and time, the unique material identifier, coding
  history), `iXML` and `axml` (project data from field recorders and
  broadcast systems, sometimes with a location), XMP (`_PMX`), ID3v2 tags
  in `id3 ` chunks, cue points with their labels and notes, sampler loops
  and every other chunk.
- **FLAC**: Vorbis comments (with the encoder's vendor string), PICTURE
  blocks, APPLICATION blocks, the seek table and cue sheet, padding, and
  ID3v2, ID3v1 or APE tags that some programs add around the stream.
- **Embedded pictures** (cover art in any of these tags) are read with
  metaex's image readers, so a cover photo with GPS coordinates is reported
  like any other photo with GPS coordinates.
- **Hidden data**: anything in tag padding, padding chunks and blocks that
  is not zero, bytes between or after the audio frames, and data after the
  end of a WAV's RIFF data. Players skip all of it, so it can hide anything.

**What strip removes and keeps**

- **Removed**: every tag (ID3v2, ID3v1, APE, Lyrics3, Vorbis comments,
  RIFF INFO, `bext`, `iXML`, `axml`, XMP), every picture and embedded file,
  cue point labels, padding, unknown chunks and blocks, and any hidden data.
- **Kept**: the audio. MP3: every audio frame, in order. WAV: the `fmt`
  and `data` chunks and the sample count (`fact`). FLAC: STREAMINFO (with
  the MD5 of the audio, which lets players check it), the seek table and
  every byte of the audio frames.
- **Kept by default because players use them**, removed with `--paranoid`:
  - MP3: the Xing/Info or VBRI frame with the LAME tag. Players need it to
    show the right duration, to seek in variable-bitrate files and for
    gapless playback; it names the encoder (for example "LAME3.100"), and
    the summary says so. Without it, players may show a less accurate
    duration and seek less precisely.
  - WAV: cue points (markers) and sampler loops, rebuilt with their
    positions only: their text labels are always removed, and so are the
    sampler's manufacturer, product and vendor data.
  - FLAC: the cue sheet (track positions; it can hold the disc's catalog
    number and the tracks' ISRC codes). Its reserved bytes are set to zero.

**What strip refuses** (nothing is written): damaged files (a truncated
frame or chunk, a tag that goes past the end of the file, a broken metadata
block, frames that don't add up to the length STREAMINFO gives), tags whose
frames can't be read, data hidden inside a WAV's `fmt` chunk (that chunk is
copied byte for byte, so it can't be cleaned), and the audio formats listed
under [Planned](#planned-not-implemented-yet).

**How verify checks**: the cleaned file is read again from scratch. It may
hold only the audio and what is kept by design; then the MP3 frames, the
WAV `fmt` and `data` chunks, or the FLAC STREAMINFO and frames are compared
byte for byte (by SHA-256) with the original. For PCM WAV files, the frames
are also read with Python's own `wave` module from both files and compared.

Real output for an MP3 generated by the test suite (all values are made
up; the same file is `tests/samples/mp3/kitchen_sink.mp3`):

```
$ metaex risk song.mp3
metaex risk: song.mp3
30 critical, 46 high, 21 medium, 35 low

CRITICAL (30)
  ID3v2.3 tag 1 (at offset 0): User text: Contact  jane.doe@example.com
  ID3v2.3 tag 1 (at offset 0): Private data: WM/UniqueFileIdentifier  AMGa_id=R 123456;AMGp_id=P 7890
  ID3v2.3 tag 1 (at offset 0): Popularimeter (rating by e-mail): jane.doe@example.com  rating 196/255, play count 42
  ID3v2.3 tag 1 (at offset 0): Ownership (purchase)  price paid 'EUR0.99', purchased 20240501, seller 'Example Music Store'
  ID3v2.3 APIC 1 (JPEG): GPS: Coordinates   48.858367, 2.294481
  Hidden data in tag padding: ID3v2.3 tag at offset 0  11 non-zero bytes in 64 bytes of padding
  ...                                       (output shortened here)

HIGH (46)
  ID3v2.3 tag 1 (at offset 0): Artist       Jane Doe
  ID3v2.3 tag 1 (at offset 0): Comment      Recorded on my phone, first take
  ID3v2.3 tag 1 (at offset 0): Picture (front cover)  front cover, image/jpeg, 2920 bytes, description 'Cover'
  APEv2 tag (at offset 36104): Record Location  12 Example Street
  ...                                       (output shortened here)

Run "metaex strip song.mp3" to remove these.
```

```
$ metaex strip song.mp3
metaex strip: song.mp3
Wrote song_clean.mp3 (31.4 KiB; original 38.9 KiB)

Removed:
  ID3v2 tags                      24 fields: Title, Artist, Album, Composer, ...
  APE tag                         8 fields: Title, Artist, Comment, Year, ...
  Lyrics3 tag                     5 fields: Indications, Lyrics, Lyrics author, Artist, ...
  ID3v1 tag                       11 fields: Title, Artist, Album, Year, ...
  Embedded pictures and objects   3 fields: ID3v2.3 APIC 1, ID3v2.3 GEOB 1, APEv2 Cover Art (Front)
  GPS location                    16 fields: Coordinates, Latitude, Longitude, OpenStreetMap, ...
  Hidden data in tags             1 field: ID3v2.3 tag at offset 0
  ...                                       (output shortened here)

Kept by design:
  Audio: Format = MPEG-1 Layer III, Sample rate = 44100 Hz, Channels = joint stereo, Bitrate = 128 kbit/s (constant), Duration = 1.985 s, Audio frames = 76, Header flags = original
  Info frame: Information frame = Info (constant bitrate), Frame count = 77, Byte count = 32181, Seek table = yes, Quality = 0, Encoder = LAME3.100, Encoder delay = 576 samples, Encoder padding = 1152 samples, Lowpass filter = 17000 Hz, LAME tag CRC = valid
  Warning: the Info frame with the LAME tag (encoder 'LAME3.100') is kept for seeking and gapless playback; use --paranoid to remove it

Verification: CLEAN
  Audio frames unchanged (SHA-256 ff6b3eb2a88e1b42b15a4231feb639f5cc44b9b2133218bdf4b5ea52eb2689e4)

File timestamps: new (not copied from the original)
Windows Zone.Identifier stream: none (no download origin attached)
```

WAV and FLAC files (`metaex strip take.wav`, `metaex strip track.flac`)
work the same way.

## Archives

metaex reads and cleans ZIP and TAR archives and gzip, bzip2 and xz files:
compressed TAR archives (`.tar.gz`, `.tgz`, `.tar.bz2`, `.tbz2`, `.tar.xz`,
`.txz`) and single compressed files (`photo.jpg.gz`). Nothing is ever
extracted to disk. An archive holds two kinds of metadata: its own (who
packed it, when, on which computer) and that of the files inside it.

**What metaex finds in the archive itself**

- **Entry times**: when each file was last modified, and in ZIP extra
  fields and TAR pax records also when it was last opened, changed and
  created.
- **Owners**: user and group names (TAR, often your login name) and
  numbers.
- **The system that made the archive** and **permissions** that are not the
  usual ones (for example 600, "only me").
- **Comments**: the archive comment and comments on entries.
- **Files your operating system adds by itself**: the `__MACOSX/` folder
  and the AppleDouble `._` files macOS adds next to every file, `.DS_Store`
  (the folder's Finder settings, which can list files that were in it),
  Windows `Thumbs.db` (thumbnails of pictures that were in the folder) and
  `desktop.ini`.
- **Extended attributes**, from AppleDouble files and TAR pax records, most
  importantly the **quarantine record** (which program downloaded the file,
  and when) and the **"where from" URLs**: the address a file was
  downloaded from and the page that linked to it.
- **Names that reveal a user folder** (`home/<name>/...`,
  `Users/<name>/...`) and **link targets**.
- **gzip headers**: the original file name, its modification time, the
  operating system, a comment and extra fields.
- **Hidden data**: anything before, between or after a ZIP archive's
  entries, in unused parts of TAR headers, in the padding after a file, or
  after the end of the archive or of the compressed data; and in a TAR
  archive, earlier copies of a file that a later file with the same name
  replaces (extracting shows only the last one).

**The files inside** are read too, by `show`, `risk` and `verify`, with the
same readers metaex uses for those formats on their own: a photo with GPS
coordinates in a ZIP archive is reported like any other photo with GPS
coordinates, under its path in the archive. Archives inside archives are
read up to 3 levels deep.

**What strip removes and keeps**

`strip` writes a new archive. Every file is kept, in the same order, with
the same name and the same contents, and gets neutral values: owner 0
with no user or group name, the time 1980-01-01 00:00, permissions 644
(755 for folders and for files that were executable: the executable bit is
kept, the rest of the permissions are not). Removed: everything listed
above, including the operating system files (each one is listed in the
summary). ZIP entries keep their compressed data as it is; TAR archives are
written as plain ustar archives; gzip files get a header with no name, no
time and no comment. A compressed file is compressed again only if what it
holds changed.

**Without `--deep`, the files inside are not changed.** If they contain
metadata, `strip` still writes the archive and names them in a warning, and
`strip` and `verify` give the same verdict: "NOT CLEAN: archive metadata
clean; 2 file(s) inside still contain metadata (not cleaned, use --deep)"
(`verify` exits with 3). **`--deep` cleans them too**, each with its
own format's rules (`--paranoid` applies to them as well), and checks each
cleaned file against the original one inside. Files metaex can't clean
(other formats, damaged files, archives nested more than 3 levels deep)
are copied unchanged and listed in a warning, never dropped silently.

```
$ metaex strip --deep plans.zip -o plans_clean.zip
metaex strip: plans.zip
Wrote plans_clean.zip (4.3 KiB; original 9.2 KiB)

*** WARNING ***
entry names reveal the user folder of 'jane.doe' (home/<name>/... or Users/<name>/...); metaex keeps file names as they are. Move the files out of the user folder and create the archive again to remove it

Removed:
  Hidden data in the archive      93 B
  Comments                        2 fields: Archive comment, Comment on plans/floor plan.jpg
  Entry times                     11 fields: plans, plans/floor plan.jpg, plans/letter.docx, plans/.DS_Store, ...
  Owners                          1 field: Owner user/group IDs
  System and permissions          2 fields: Created on, Permissions
  Operating system files          6 fields: plans/.DS_Store, plans/Thumbs.db, plans/desktop.ini, __MACOSX, ...
  Extended attributes             2 fields: plans/floor plan.jpg: com.apple.quarantine, plans/floor plan.jpg: com.apple.metadata:kMDItemWhereFroms
  Other entry information         1 field: plans/letter.docx: Windows attributes
  Camera and owner                3 fields: Make, Model, BodySerialNumber
  Camera settings and other EXIF  6 fields: Orientation, ExposureTime, FNumber, ISOSpeedRatings, ...
  Software                        1 field: Software
  Dates                           3 fields: DateTime, DateTimeOriginal, OffsetTimeOriginal
  GPS location                    8 fields: Coordinates, Latitude, Longitude, OpenStreetMap, ...
  Thumbnail                       3 fields: Embedded thumbnail, Dimensions, Compression
  XMP                             13 fields: XMP packet, xmp:CreatorTool, xmpMM:DocumentID, xmpMM:OriginalDocumentID, ...
  Document properties             15 fields: Title, Subject, Author, Keywords, ...
  Application properties          20 fields: Template, Total editing time (minutes), Pages, Words, ...

Kept by design:
  Archive contents: Format = ZIP, Entries = 5, Folders = 1, Files = 4, Uncompressed size (bytes) = 3795
  Names that reveal a user folder: Users/jane.doe/Desktop/todo.txt = user folder of 'jane.doe'
  Warning: --deep copied 2 file(s) inside unchanged, because metaex can't clean them: Users/jane.doe/Desktop/todo.txt (type not recognized), run.sh (type not recognized)
  Warning: removed 6 operating system file(s): plans/.DS_Store, plans/Thumbs.db, plans/desktop.ini, __MACOSX, __MACOSX/plans, __MACOSX/plans/._floor plan.jpg

Verification: CLEAN
  Archive contents (2 file(s) inside cleaned and verified): the same files as the original (SHA-256 63f10701c06540c34e5831ce7b2fdc2585bcc8dcd7252cd2185be7ba9796ebe3)

File timestamps: new (not copied from the original)
Windows Zone.Identifier stream: none (no download origin attached)
Note: --deep cleaned 2 file(s) inside: plans/floor plan.jpg (JPEG), plans/letter.docx (DOCX)
Note: data before the start of the ZIP archive
```

`metaex verify plans_clean.zip --original plans.zip` checks the result on
its own: no metadata left in the archive or in the files inside, the same
files in the same order, and each file inside either byte for byte the
original or a cleaned copy that verifies against it.

**Refused** (nothing is written): damaged or truncated archives, names
that would be extracted outside the folder (`../`, absolute paths), links
that point to an absolute path, outside the archive or into a user folder,
ZIP archives with encrypted entries or that are signed (JAR, APK and other
signed archives), TAR archives with sparse files, xz files with anything
but padding after their data, and archives that would expand to more than
1 GiB or over 200 times their size (a "zip bomb").

## Many files and folders

`show`, `risk`, `strip` and `verify` accept several files and folders at
once:

```
metaex risk photos --recursive --quiet                     # what a whole folder reveals
metaex strip photos --recursive --output-dir clean         # clean copies in another folder
metaex verify clean --original photos --recursive          # check them all
metaex risk photos --recursive --csv > findings.csv        # every finding, for a spreadsheet
metaex strip a.jpg b.png report.docx                       # a_clean.jpg, b_clean.png, ...
```

**Which files.** Files you name are always processed. Folders are searched
one level deep; `--recursive` (`-r`) also searches their subfolders. Inside
folders metaex leaves out, and lists in the summary:

- hidden files and folders (names starting with `.`, and files with the
  Windows "hidden" attribute); `--include-hidden` includes them;
- `.git`, `.hg`, `.svn`, `node_modules`, `__pycache__` and Python virtual
  environments, always (even with `--include-hidden`);
- symbolic links and Windows junctions: they are never followed;
- metaex's own cleaned copies (`*_clean.*`), so running `strip` twice never
  cleans a cleaned copy again (`verify` does check them). Copies made with
  `--rename` have random names, so a later run can't tell them apart from
  originals;
- the `--output-dir` folder, when it is inside a folder being cleaned;
- files metaex doesn't support ("unsupported format"; see `metaex formats`).
  A file you name yourself that metaex doesn't support is refused by
  `strip` and `verify`, as for a single file.

Every file is processed once, even if two arguments reach it, in an order
sorted by path, so the output is the same on every system.

**Where cleaned copies go.** Next to each original (`photo.jpg` ->
`photo_clean.jpg`), as for a single file. With `--output-dir DIR` they go
into `DIR` instead (created if missing), with their original names and the
same folder structure (`photos/2024/beach.jpg` -> `clean/2024/beach.jpg`).
`-o`/`--output` names a single output file, so it only works with one
file. A cleaned copy that already exists is skipped and listed; add
`--overwrite` to replace it. metaex never writes a cleaned copy over an
original, refuses an `--output-dir` that is one of the folders being
cleaned, and compares paths the way the system does (ignoring case on
Windows and macOS). `--in-place` replaces the originals after one
confirmation for the whole run ("Type 'yes' to overwrite 6 files"), or
without asking with `--yes`. `--rename`, `--deep`, `--paranoid` and
`--fixed-time` work per file; options for some formats only
(`--deep`, `--rebuild-image-data`, `--remove-comments`) apply to the files
of those formats. Each file is still written to a temp file, verified and
only then moved into place.

**One bad file never stops the run.** Each file gets an outcome:
`cleaned`, `already clean` (it had nothing to remove; the verified copy is
still written), `skipped`, `refused` (damaged, unsupported or not safe to
clean) or `error` (could not be read or written). `verify` reports `clean`
or `not clean` per file, and `show` and `risk` report `examined`. Pressing
Ctrl+C stops after the current file, leaves no temp files and prints the
summary so far, marked as partial.

Real output (the files are samples from the test suite; on Windows the
paths are printed with `\`):

```
$ metaex strip photos --recursive --output-dir clean
[1/7] cleaned: photos/2024/beach.jpg -> clean/2024/beach.jpg
  Warning: ICC profile names a device (ACME X100); use --paranoid to remove it (colors may shift)
[2/7] cleaned: photos/2024/report.docx -> clean/2024/report.docx
  Warning: *** the document still contains tracked changes, and 14 characters of DELETED TEXT are still stored in the file, where anyone can read them. Accept or reject all changes in Word (Review > Accept > Accept All Changes) or LibreOffice (Edit > Track Changes > Accept All), save, and run metaex again.
  Warning: kept 1 comment(s) (their authors are now "Author"); use --remove-comments to remove them
  ... (three more warnings about this document)
[3/7] cleaned: photos/gps_camera.jpg -> clean/gps_camera.jpg
[4/7] skipped: photos/notes.txt: unsupported format (detected type: unknown)
[5/7] already clean: photos/orientation_6.jpg -> clean/orientation_6.jpg
[6/7] cleaned: photos/text_chunks.png -> clean/text_chunks.png
[7/7] refused: photos/truncated.jpg: missing end-of-image (EOI) marker; the file may be truncated; no file written

Summary: 8 entries
  cleaned        4
  already clean  1
  skipped        2
  refused        1
  error          0

By format:
  DOCX        1 (1 cleaned)
  JPEG        4 (2 cleaned, 1 already clean, 1 refused)
  PNG         1 (1 cleaned)
  unknown     1 (1 skipped)
  (not read)  1 (1 skipped)

Refused (1):
  photos/truncated.jpg: missing end-of-image (EOI) marker; the file may be truncated; no file written

Skipped (2):
  photos/.DS_Store: hidden (use --include-hidden to include it)
  photos/notes.txt: unsupported format (detected type: unknown)

Run "metaex formats" to see the supported formats.
```

**The summary** after every run with several files counts the files per
outcome and per format, then lists each refused, failed and skipped file
with the reason. For `risk` it also counts the findings per risk level and
lists the files with critical findings, so `metaex risk photos -r --quiet`
is a quick overview of a whole folder. `--quiet` (`-q`) prints only the
summary (and the files that were refused or failed, on stderr) instead of
a line or a report per file. Without `--quiet`, `show` and `risk` print each
file's report under a `==> [3/7] path` header, and `strip` and `verify` one
line per file.

**Checking a folder of cleaned copies.** `metaex verify clean --original
photos --recursive` pairs each cleaned file with the original at the same
path inside `photos` (`photo_clean.jpg` is also paired with `photo.jpg`),
compares their image or document data, and lists the files without a pair:
originals that have no cleaned copy (for example because `strip` refused
them) and cleaned files without an original (checked for metadata only).

**Exit codes** for a run with several files: **1** if any file was refused
or failed (or the run was interrupted), else **3** if `verify` found a file
that is not clean, else **0**. For a single file they are the same as
before.

**JSON and CSV.** `--json` prints one object with every file (its path,
outcome, format, reason and the same details as for a single file) and the
summary. `--csv` (for `show`, `risk` and `verify`) prints one row per
finding: path, format, section, field, value and risk level (`show` lists
every field, including file system information and hashes, which have no
risk level; `verify` lists what was left and the problems found). The file
is UTF-8 with a byte order mark, so Excel opens it with the right
characters, and cells are quoted where needed. Metadata and file names come
from untrusted files, so any cell that starts with `=`, `+`, `-`, `@`, a
tab or a carriage return gets a leading `'`, which makes Excel, LibreOffice
and Google Sheets show it as text instead of running it as a formula. With
several files the summary goes to stderr, so the output stays a valid CSV
file. With `--json` or `--csv` the per-file lines also go to stderr.

**Supported formats.** `metaex formats` lists the formats metaex
supports, with their extensions and what `strip` removes from each, and the
formats it refuses, with the reason (`--json` too).

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

Each format's own limits are listed in [FORMATS.md](FORMATS.md).

## License

MIT. See [LICENSE](LICENSE).
