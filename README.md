# metaex

**metaex** is a command-line tool that shows and removes metadata from
files: photos, documents, audio and archives. It is written in Python and
uses only the standard library, so there is nothing to install besides
Python itself.

> **Status: work in progress. The supported formats so far are images:
> JPEG, PNG, GIF, WebP and SVG.** For these, metaex can show, assess, strip
> and verify metadata. For every other format it only shows file system
> information (size, dates, hashes) and refuses to strip. The other formats
> listed below are planned, not done.

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

### Planned (not implemented yet)

| Category  | Formats                   |
|-----------|---------------------------|
| Documents | DOCX, XLSX, PPTX, ODT     |
| Audio     | MP3, WAV, FLAC            |
| Archives  | ZIP, TAR                  |

`metaex show` already recognizes these (and others) by their content, but
does not read their embedded metadata yet; `metaex strip` refuses them and
writes nothing.

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
```

PNG, GIF, WebP and SVG files work the same way (`metaex strip image.png`
writes `image_clean.png`). The examples below are real output for a JPEG
generated by the test suite (all values are made up); examples for the
other formats follow in [PNG example](#png-example),
[GIF example](#gif-example), [WebP example](#webp-example) and
[SVG example](#svg-example).

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
property found in both standard and Extended XMP is counted once. On a terminal the levels are colored; use `--no-color`
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

Verification: clean
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
  orientation (the image may display rotated).
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
  exists, metaex refuses (use `-o` to choose a name). `--in-place` replaces
  the original, after typing `yes` (or with `--yes`).
- **Malformed files are refused**, never partly cleaned. For PNG that
  includes any chunk with a bad CRC, a missing IEND, critical chunks metaex
  doesn't know and image data that is too short or invalid; for GIF a
  missing trailer; for WebP a RIFF size larger than the file and images
  with more or fewer than one bitstream; for SVG anything that is not
  well-formed XML.

Other options: `-o OUTPUT`, `--rename` (random name like
`img_8f3a2c1d.jpg`), `--fixed-time` (sets the file's modified/accessed time
to 2000-01-01T00:00:00Z; the creation time is set by the operating system),
`--json`.

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
with each embedded image replaced by the hash of its own image data.

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

Verification: clean
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

Verification: clean
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

Verification: clean
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

Verification: clean
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

Specific to how metaex cleans JPEGs:

- Because the image is never re-encoded, its **quantization tables** are
  kept as they are. They can hint at which camera model or editing software
  produced the file.
- Data hidden **inside the compressed image data** itself (steganography)
  cannot be detected or removed without re-encoding.
- Unusual JPEG variants (hierarchical or extension markers) are refused
  rather than cleaned.

Specific to how metaex cleans PNGs:

- Extra bytes inside the image data (after the pixels or after the end of
  the compressed stream) are detected. Data hidden *in the pixels
  themselves* (steganography) or in unused palette entries is not.
- Images whose decompressed data would be larger than 1 GiB are not
  checked for hidden data (show and verify say so); they are stripped as
  before, with the image data copied as it is.
- Hidden data in APNG frames (fdAT) is detected but can't be rebuilt; such
  files are refused.
- Stripping removes **Content Credentials** (C2PA, the caBX chunk). Some
  websites use them to label AI-generated or edited images; a cleaned copy
  no longer carries that provenance.
- The kept ICC profile keeps its description and copyright text and any
  device names (the summary warns about device names). Use `--paranoid` to
  drop it. The same applies to GIF and WebP.

Specific to GIF:

- The LZW image data is kept as it is and not decoded, so bytes hidden
  after its end code, in unused color table entries or in the pixels are
  not detected.
- Plain Text extensions (text drawn over the image, which almost no viewer
  supports) are removed with the other metadata.

Specific to WebP:

- The VP8/VP8L bitstreams and alpha data are copied as they are and not
  decoded; data hidden inside them is not detected.
- The canvas size and the alpha flag are copied from the original VP8X
  chunk; a simple (VP8 or VP8L only) file becomes an extended one only if
  it keeps something that needs VP8X (its EXIF orientation, or a stray ICC
  profile).

Specific to SVG:

- **The file is re-serialized**, not byte-copied (see [strip](#strip)): the
  drawing is the same, but a byte comparison with the original is not.
- **Visible text in the drawing is not metadata**: names in `<text>`,
  element `id`s and class names ("jane-notes"), and text that is drawn
  are all kept. So are scripts and external references (metaex warns
  about them); remove them yourself if you don't want them.
- `<title>` and `<desc>` are kept by default (for accessibility); the
  strip summary shows them, and `--paranoid` removes them.
- Processing instructions are removed, including `<?xml-stylesheet?>`: a
  drawing that takes its styles from an external stylesheet will look
  different. Copy the CSS into a `<style>` element first.
- Only the official W3C SVG 1.0 and SVG 1.1 DOCTYPE lines are accepted
  (quotes and spacing may differ). Every other DOCTYPE is refused: SVG
  Tiny or Basic, a different or `https://` address, a DOCTYPE with extra
  declarations in `[...]`, more than one DOCTYPE. So are ENTITY
  declarations, named entities other than `&amp;` `&lt;` `&gt;` `&quot;`
  `&apos;` (for example `&nbsp;` or `&copy;`; write the character itself
  or `&#160;` instead) and any encoding other than UTF-8. Embedded SVGs
  are read at most 3 levels deep.
- data: URIs inside CSS (`url(data:...)`, often fonts) are not examined;
  `show` warns about them and `strip` refuses such files.
- Only the editor namespaces metaex knows are recognized (Inkscape,
  Sodipodi, Adobe, Sketch, Affinity, Visio, Krita); data a program writes
  without its own namespace (for example `data-*` attributes) is kept.

## License

MIT. See [LICENSE](LICENSE).
