# Supported formats

What metaex does with each format it supports: what it reads, what `strip`
removes, what it keeps on purpose, what the options change, how `verify`
checks the result, what it refuses and its known limits. The
[README](README.md) explains how to use metaex; `metaex formats` prints a
short version of this list.

- [Images](#images): [JPEG](#jpeg), [PNG](#png), [GIF](#gif), [WebP](#webp), [SVG](#svg)
- [Office documents](#office-documents): [DOCX, XLSX and PPTX](#docx-xlsx-and-pptx),
  [ODT, ODS and ODP](#odt-ods-and-odp)
- [Audio](#audio): [MP3](#mp3), [WAV](#wav), [FLAC](#flac)
- [Archives](#archives): [ZIP](#zip), [TAR](#tar), [gzip, bzip2 and xz](#gzip-bzip2-and-xz)
- [Refused formats](#refused-formats)

## What holds for every format

- **Whitelist.** `strip` keeps only what the format needs to show or play
  the file and drops everything else, including data metaex doesn't
  recognize.
- **The content is copied, not re-encoded.** Image data, document parts
  that don't change and audio frames are copied byte for byte, so there is
  no quality loss. SVG is the one exception (it is text, and is written
  again; see [SVG](#svg)).
- **Verified before it exists.** The cleaned copy is written to a temporary
  file next to the destination, read again from scratch, checked for
  leftovers and compared with the original. Only then is it moved into
  place; if anything fails, nothing is written.
- **Damaged files are refused**, never partly cleaned.
- **Nothing is overwritten**: the cleaned copy is `<name>_clean.<ext>`
  unless you choose otherwise (`-o`, `--output-dir`, `--in-place`).
- **File system information** (dates, owner, the Windows "downloaded from
  the internet" mark) is not copied: the cleaned copy is a new file. It is
  shown by `show` but is not part of the file; see the README's
  limitations.
- **Limits.** Files over 512 MiB are refused (`--max-size` changes that).
  Every count, length and nesting depth read from a file is capped, and
  compressed data is decompressed with a size limit, so a crafted file
  can't exhaust memory or make metaex hang.

`--paranoid` removes the few things that are kept by default because they
change how a file looks or plays; each format below says what that is.

## Images

**Color profiles (ICC)**, kept by default in JPEG, PNG, GIF and WebP (and
removed with `--paranoid`), are copied byte for byte, so a profile is kept
only if it holds nothing but color data: every tag a known color tag (no
private tags, no `meta` dictionary), every byte part of the header, the
tag table or a tag, and the header's reserved bytes zero. Any other
profile is listed by `show` and `risk` ("Data metaex can't check") and
removed by `strip`; `verify` reports it as a leftover.

### JPEG

Extensions: `.jpg`, `.jpeg`, `.jpe`, `.jfif`.

- **Reads**: EXIF (camera, settings, dates, GPS, serial numbers, the
  embedded thumbnail), XMP and Extended XMP, IPTC and Photoshop resources
  (including the Photoshop thumbnail), ICC profiles, MPF extra images,
  JFIF/JFXX, comments, unknown APP segments and data after the end of the
  image.
- **strip keeps** the frame header, quantization and Huffman tables, the
  restart interval, the scan headers and the scan data, byte for byte.
- **strip removes** EXIF, XMP, IPTC/Photoshop, thumbnails, MPF extra
  images, comments, unknown segments and any data after the image.
- **Kept by design**: the EXIF **orientation**, only if the image is
  rotated or mirrored (written as a new EXIF with that one tag); the **ICC
  color profile** (the summary warns if it names a device); a new minimal
  **JFIF** header (version and density); the **Adobe** segment CMYK images
  need.
- **Options**: `--paranoid` also removes the ICC profile and the JFIF
  density (colors may shift). `--no-orientation` drops the orientation (the
  image may then display rotated).
- **verify** compares the image data (frame, tables and scan data) with the
  original by SHA-256.
- **Refused**: truncated files, unusual variants (hierarchical JPEG,
  extension markers).
- **Known limits**: the quantization tables are kept as they are and can
  hint at the camera or software that made the file. Data hidden inside the
  compressed image data (steganography) can't be detected or removed
  without re-encoding.

### PNG

Extension: `.png`.

- **Reads**: text chunks (tEXt, zTXt, iTXt, any keyword), AI generation
  data (Stable Diffusion "parameters", ComfyUI "prompt"/"workflow",
  InvokeAI), XMP, EXIF (eXIf, including GPS), the modification time
  (tIME), the ICC profile (iCCP), color and display chunks (sRGB, gAMA,
  cHRM, cICP, mDCv, cLLI, pHYs, bKGD, sBIT, hIST, sPLT, oFFs, pCAL, sCAL),
  Content Credentials (caBX: reported, not parsed), APNG animation chunks,
  unknown chunks and data after the end of the image. Every chunk's CRC is
  checked. The image data is decompressed piece by piece and compared with
  the exact size the image needs, which finds data hidden in the compressed
  stream.
- **strip keeps** IHDR, PLTE, tRNS, every IDAT, IEND and the APNG chunks
  (acTL, fcTL, fdAT), in their original order.
- **strip removes** every text chunk (AI data and XMP included), tIME,
  eXIf, caBX, bKGD, sPLT, hIST, sBIT, oFFs, pCAL, sCAL, unknown chunks and
  any data after IEND.
- **Kept by design**: the ICC profile (renamed to the generic "ICC
  profile"), the EXIF orientation of a rotated image, and the color chunks
  sRGB, gAMA, cHRM, cICP, mDCv, cLLI and the pixel density pHYs, each only
  with its exact expected size.
- **Options**: `--paranoid` also removes the ICC profile, gAMA, cHRM, cICP,
  mDCv, cLLI and pHYs (sRGB stays). `--no-orientation` as for JPEG.
  `--rebuild-image-data`: a PNG with hidden data in its image stream is
  refused unless you pass this option; metaex then decompresses exactly the
  bytes the image needs and compresses them again (zlib level 9). PNG
  compression is lossless, so the pixels are identical, and `verify` says
  "image data rebuilt, pixels identical".
- **verify** compares IHDR, PLTE, tRNS, the joined IDAT data and the APNG
  frame chunks (or, after `--rebuild-image-data`, the decompressed pixels).
- **Refused**: a bad CRC, a missing IEND, critical chunks metaex doesn't
  know, image data that is too short or invalid, hidden data in APNG frames
  (it can't be rebuilt), and images whose decompressed data would be larger
  than 1 GiB: they are not checked for hidden data, so `show` warns and
  `verify` says NOT CLEAN.
- **Known limits**: data hidden in the pixels themselves or in unused
  palette entries is not detected. Removing Content Credentials removes the provenance some
  websites use to label AI-generated or edited images. A kept ICC profile
  keeps its description and copyright text.

### GIF

Extension: `.gif`.

- **Reads**: comments, Plain Text extensions, XMP (with its "magic
  trailer" or in ordinary sub-blocks), the ICC profile (ICCRGBG1012), the
  loop count (NETSCAPE2.0 / ANIMEXTS1.0), the frame count, every other
  application or unknown extension (by identifier) and data after the
  trailer.
- **strip keeps** the header, screen descriptor, color tables, every image
  (descriptor, local color table, LZW data) and the trailer. Each image's
  Graphic Control extension (timing, transparency) and the loop count are
  written again in their minimal form.
- **strip removes** comments, Plain Text extensions, XMP, every other
  application and unknown extension, and data after the trailer.
- **Kept by design**: the animation (frames, timing, loop count) and the
  ICC profile.
- **Options**: `--paranoid` also removes the ICC profile.
- **verify** compares the screen descriptor, color tables, Graphic Control
  fields, image descriptors, LZW data and loop count.
- **Refused**: a missing trailer, broken sub-blocks, image data that is not
  valid LZW, and image data that hides more than the image: codes for more
  pixels than it has, or data after the end code (viewers ignore both, and
  the image data is copied byte for byte). The LZW codes are walked without
  building the pixels; files with more than 32 million codes are not
  checked, so they are refused too.
- **Known limits**: data hidden in unused color table entries or in the
  pixels themselves is not detected. Plain Text extensions (text drawn over the image, which almost
  no viewer shows) are removed.

### WebP

Extension: `.webp`.

- **Reads**: EXIF (including GPS, with or without an "Exif\0\0" prefix),
  XMP, the ICC profile (ICCP), animation (ANIM and ANMF frames, including
  chunks inside frames), unknown chunks, the VP8X flags (and whether they
  match the chunks present) and data after the RIFF data.
- **strip keeps** VP8, VP8L, ALPH, ANIM and the ANMF frames (only their
  image sub-chunks). A new VP8X chunk is written whose flags match the
  chunks kept; the RIFF size is recomputed and padding bytes are zero.
- **strip removes** EXIF, XMP, unknown chunks (also inside frames) and data
  after the RIFF data.
- **Kept by design**: the animation, the ICC profile, the EXIF orientation
  of a rotated image.
- **Options**: `--paranoid` also removes the ICC profile;
  `--no-orientation` as for JPEG.
- **verify** compares the canvas size, the image chunks and the frames.
- **Refused**: a RIFF size larger than the file, images with more or fewer
  than one bitstream, and data after the end of a lossless bitstream (VP8L,
  or alpha data in ALPH), which viewers never read: metaex reads lossless
  bitstreams to their end (without building the pixels) and refuses
  invalid ones, and files with more than 16 million lossless symbols, which
  it doesn't check.
- **Known limits**: lossy VP8 bitstreams are not decoded, so data hidden
  at the end of one, or in any image's pixels, is not detected. The canvas
  size and alpha flag are copied from the original VP8X chunk.

### SVG

Extension: `.svg`.

- **Reads**: `<metadata>` (RDF / Dublin Core: creator, date, rights,
  publisher, ...), `<title>` and `<desc>`, comments, processing
  instructions, editor data (Inkscape/Sodipodi, Adobe Illustrator, Sketch,
  Affinity, Visio and Krita namespaces: document names, export file names,
  program versions, window sizes, layer names), **absolute local file
  paths** in any attribute (`C:\Users\<name>\...`, `/home/<name>/...`,
  `/Users/<name>/...`, `file:///...`) and **embedded images** (data: URIs),
  which are read with their own format's rules, so a JPEG's GPS inside an
  SVG is found too. It warns about `<script>`, event handler attributes and
  external `http(s)` references.
- **strip writes the file again** from the parsed drawing instead of
  copying bytes (SVG is text). What is kept comes out as parsed and
  namespace prefixes stay exactly as written (never `ns0:`), but
  formatting inside tags can change: quoting, `<a></a>` written as `<a/>`,
  the indentation left by removed elements.
- **strip removes** everything that is not part of the drawing (a
  whitelist: it keeps SVG elements, the attributes used to draw plus
  `role`, `aria-*` and event handlers, `xlink:` and `xml:` attributes, and
  text inside text elements). That is: the standard DOCTYPE line,
  `<metadata>`, comments, processing instructions (the XML declaration
  stays), every element and attribute in an editor namespace, elements in
  other namespaces or that are not SVG elements, attributes no renderer
  uses (`data-*`, a program's own, such as draw.io's `content`), text
  outside text elements, and declarations of namespaces no longer used.
  show and risk list these under "Data that is not part of the drawing",
  and verify reports them as leftovers. Embedded images are stripped with
  their own format's rules and written back as base64. Paths in editor attributes
  (`inkscape:export-filename`, `sodipodi:absref`) are removed.
- **Kept by design**: `<title>` and `<desc>`, because screen readers use
  them (the summary shows what they say).
- **Options**: `--paranoid` also removes `<title>` and `<desc>`;
  `--paranoid` and `--no-orientation` apply to embedded images.
- **verify** compares the **drawing**: a canonical form of every element,
  attribute and text that is drawn (by namespace, not prefix, leaving out
  what strip removes), with each embedded image replaced by the hash of its
  image data.
- **Refused**: anything that is not well-formed XML; a DOCTYPE other than
  the official W3C SVG 1.0 or 1.1 one; ENTITY declarations; named entities
  other than `&amp;` `&lt;` `&gt;` `&quot;` `&apos;` (such as `&nbsp;`);
  any encoding other than UTF-8; files that link to local files (an
  `<image>` pointing into `C:\Users\<name>\...`: removing it would break the
  drawing); embedded data that can't be stripped (unsupported or damaged
  images, data: URIs inside CSS); files whose root element is not in the
  SVG namespace (nothing in them is an SVG drawing); inside
  `<foreignObject>`, anything but HTML text layout (images, frames, other
  namespaces). The DTD a DOCTYPE points to is never downloaded.
- **Known limits**: a byte comparison with the original no longer matches.
  Visible text, element `id`s and class names are kept, and so are scripts
  and external references (metaex warns). `<?xml-stylesheet?>` is removed
  with the other processing instructions, so a drawing that takes its
  styles from an external stylesheet will look different. Embedded SVGs
  are read at most 3 levels deep.

## Office documents

Office documents are ZIP packages of XML files. metaex **edits** them
instead of rebuilding them: files that don't change are copied with their
compressed bytes as they are, and in the files that do change only the
bytes that hold metadata change, so every namespace prefix and declaration
stays as written and Word, Excel, PowerPoint and LibreOffice open the
result normally. The ZIP container is written again with no comments, no
extra fields and the fixed time 1980-01-01 for every file, as Microsoft
Office does.

### DOCX, XLSX and PPTX

Microsoft Word, Excel and PowerPoint, including macro-enabled files and
templates: `.docx`, `.docm`, `.dotx`, `.dotm`; `.xlsx`, `.xlsm`, `.xltx`,
`.xltm`; `.pptx`, `.pptm`, `.potx`, `.potm`, `.ppsx`, `.ppsm`.

- **Reads**: document properties (author, last editor, creation,
  modification and print dates, revision number, total editing time, title,
  subject, keywords, description, company, manager, template, application
  and version), custom properties, sensitivity labels (Microsoft Purview,
  with your organization's tenant ID), SharePoint data, document variables,
  comments and their authors (with Microsoft 365 user IDs, often e-mail
  addresses), tracked changes and **deleted text still stored in the
  file**, revision IDs (rsids, which can link documents edited on the same
  computer), file paths and links (attached template, linked files and
  images, linked workbooks, data connections, the folder an Excel workbook
  was saved in), hidden content (hidden text, sheets and slides, speaker
  notes, the glossary), embedded images (with their own metadata),
  embedded objects and the authors of embedded Office files, macros,
  printer settings, the thumbnail, and the ZIP container's comments,
  timestamps and hidden data.
- **strip removes** every document property, custom property, sensitivity
  label, SharePoint data and document variable (`docProps/core.xml` and
  `app.xml` stay, empty); revision IDs, printer settings, the thumbnail,
  the attached template, the folder a workbook was saved in, Excel's
  version and co-authoring IDs (workbook history), and files nothing in the
  document uses. **Comment and revision authors become "Author"**, and their
  initials and dates are removed. Where the format requires a value, a
  neutral one is written: an empty string, "Author", "None", or dates from
  2000-01-01T00:00:00 one second apart in the original order (PowerPoint
  sorts comments by date). Every embedded JPEG, PNG, GIF, WebP and SVG
  image is cleaned like a separate image file.
- **Kept, with a warning**: tracked changes (metaex doesn't accept or
  reject them for you, so deleted text stays until you do, and the summary
  says so loudly), macros, hidden text, hidden sheets and slides, speaker
  notes, the glossary, comments (with "Author" as their author), links,
  data connections, embedded objects and images metaex can't clean (EMF,
  WMF, TIFF, ...). metaex can't check what embedded objects and those
  images contain, so `strip` and `verify` list them as "not checked" and
  say NOT CLEAN.
- **Options**: `--remove-comments` removes comments completely, with their
  marks in the text. `--paranoid` and `--no-orientation` apply to embedded
  images.
- **verify** compares every file in the document with the original: the
  same bytes, or for the files strip may change, the same XML once exactly
  those changes are left out; images by their image data.
- **Refused**: documents that link to files in a user's folder (removing
  the link would change the document), embedded images that can't be
  cleaned, DOCTYPE or entity declarations, damaged ZIP files, zip bombs,
  names that point outside the folder (`../`), encrypted entries,
  password-protected documents, "Strict Open XML" documents and binary
  Excel workbooks (`.xlsb`).
- **Known limits**: the visible content is not touched (names in the text,
  cells, headers and footers, slides, speaker notes). Excel writes the
  author's name at the start of each note ("Jane Doe:"); that is comment
  text and stays unless you use `--remove-comments`. Content controls that
  show a document property keep the text they show. Macros and embedded
  objects are not cleaned inside. Removing a sensitivity label may go
  against your organization's rules. Files that change are compressed
  again, so the size differs from the original.

### ODT, ODS and ODP

OpenDocument text, spreadsheets and presentations (LibreOffice and other
programs), including templates: `.odt`, `.ott`; `.ods`, `.ots`; `.odp`,
`.otp`.

- **Reads**: document properties and custom properties (`meta.xml`),
  application settings (`settings.xml`), comment and change authors and
  dates, tracked changes and deleted text, file paths and links, hidden
  content, embedded images, embedded objects, Basic macros and scripts,
  digital signatures, the thumbnail, and the ZIP container.
- **strip removes** every property (`meta.xml` stays, empty), the user
  interface and application settings, digital signatures (they no longer
  match once the document is cleaned), the thumbnail and files nothing in
  the document uses; comment and change authors become "Author", and
  change dates become neutral dates from 2000-01-01T00:00:00 in the
  original order (LibreOffice sorts changes by date). Removing a file also
  removes its manifest entry; `mimetype` stays the first, uncompressed
  file. Embedded images are cleaned like separate image files.
- **Kept, with a warning**, **options**, **verify** and **known limits**:
  as for [DOCX, XLSX and PPTX](#docx-xlsx-and-pptx).
- **Refused**: as for DOCX, XLSX and PPTX, and password-protected
  (encrypted) OpenDocument files.

## Audio

Every tag is removed, cover art included. The audio itself is never decoded
or encoded again: it stays byte for byte the same, and `verify` proves it.
Embedded pictures are read with metaex's image readers, so a cover photo
with GPS coordinates is reported like any other photo with GPS coordinates.
Anything that is not zero in padding, and any bytes between or after the
audio frames, is reported as **hidden data** (critical) and always removed.

### MP3

Extension: `.mp3`.

- **Reads**: ID3v2 tags (2.2, 2.3 and 2.4, at the start or appended at the
  end, several if there are), ID3v1 and ID3v1.1 (with the "TAG+" block),
  APEv1 and APEv2, Lyrics3, and the Xing/Info (with the LAME tag) or VBRI
  frame. Every ID3 frame is shown, including those that identify you or a
  purchase: **POPM** (a rating stored with your e-mail address), **PRIV**,
  **UFID** and **OWNE** (price, date and seller). Compressed and
  unsynchronised frames are read; encrypted ones are listed.
- **strip keeps** every audio frame, in order (not the bytes between
  them).
- **strip removes** every tag (ID3v2, ID3v1, APE, Lyrics3), every picture
  and embedded file, and any hidden data.
- **Kept by default because players use it**: the Xing/Info or VBRI frame
  with the LAME tag (duration, seeking in variable-bitrate files, gapless
  playback). It names the encoder (for example "LAME3.100"); the summary
  says so, and its unknown bytes are zeroed.
- **Options**: `--paranoid` removes that frame too (players may then show a
  less accurate duration and seek less precisely).
- **verify** compares the audio frames with the original by SHA-256.
- **Refused**: damaged files (a truncated frame, a tag past the end of the
  file), tags whose frames can't be read, AAC audio in a file named
  `.mp3`, and "free-format" MP3 (a rare variant without a fixed frame
  size).
- **Known limits**: data hidden inside the audio frames (unused bits,
  "ancillary data") is not detected, and each frame header's private bit is
  kept. Tags in places metaex doesn't read (such as an APE tag at the
  start) are removed as hidden data, not shown field by field.

### WAV

Extensions: `.wav`, `.wave`.

- **Reads**: the RIFF INFO list (artist, comments, dates, software, ...),
  the Broadcast WAV `bext` chunk (description, originator, origination date
  and time, unique material identifier, coding history), `iXML` and `axml`
  (project data from field recorders and broadcast systems, sometimes with
  a location), XMP (`_PMX`), ID3v2 tags in `id3 ` chunks, cue points with
  their labels and notes, sampler loops, every other chunk and data after
  the RIFF data.
- **strip keeps** the `fmt` and `data` chunks and the sample count
  (`fact`).
- **strip removes** every other chunk: INFO, `bext`, `iXML`, `axml`, XMP,
  ID3, cue labels and notes, padding, unknown chunks and hidden data.
- **Kept by default because players and editors use them**: cue points
  (markers) and sampler loops, rebuilt from their positions only: their
  labels are always removed, and so are the sampler's manufacturer,
  product and vendor data.
- **Options**: `--paranoid` removes the cue points and loops too.
- **verify** compares `fmt` and `data` with the original by SHA-256, and
  for PCM files also reads the frames of both files with Python's own
  `wave` module and compares them.
- **Refused**: damaged files (a wrong RIFF size, a truncated chunk), data
  hidden inside the `fmt` chunk (it is copied byte for byte, so it can't
  be cleaned): bytes after the format description, and format-specific
  bytes a format doesn't define (PCM, IEEE float, A-law and mu-law have
  none; extensible has 22) or that metaex can't check (other codecs), WAV
  files over 4 GiB (RF64, BW64).
- **Known limits**: see [all audio](#known-limits-for-all-audio).

### FLAC

Extension: `.flac`.

- **Reads**: Vorbis comments (with the encoder's vendor string), PICTURE
  blocks, APPLICATION blocks, the seek table, the cue sheet, padding, and
  ID3v2, ID3v1 or APE tags some programs add around the stream.
- **strip keeps** STREAMINFO (with the MD5 of the audio, which lets players
  check it), the seek table, and every byte from the first audio frame to
  the end of the last.
- **strip removes** Vorbis comments, pictures, application blocks,
  padding, ID3 and APE tags and hidden data.
- **Kept by default because players use it**: the cue sheet (track
  positions; it can hold the disc's catalog number and the tracks' ISRC
  codes). Its reserved bytes are set to zero.
- **Options**: `--paranoid` removes the cue sheet too.
- **verify** compares STREAMINFO and the audio frames with the original by
  SHA-256.
- **Refused**: damaged files (a broken metadata block, frames that don't
  add up to the length STREAMINFO gives).
- **Known limits**: only the first and the last audio frame are parsed (the
  last to find exactly where the audio ends); the frames in between are
  copied byte for byte without being checked, so data hidden between them
  would be kept.

### Known limits for all audio

The audio is never decoded: watermarks, data hidden in the sound itself
and anything the recording reveals (voices, the room, background noise)
are not detected and stay.

## Archives

metaex reads and cleans archives without ever extracting them to disk. An
archive holds two kinds of metadata: its own (who packed it, when, on which
computer) and that of the files inside it.

**What metaex reads in every archive**: entry times (modified, and in ZIP
extra fields and TAR pax records also accessed, changed and created),
owners (user and group names and numbers), the system that made it,
unusual permissions, comments, files the operating system adds by itself
(`__MACOSX/`, AppleDouble `._` files, `.DS_Store`, `Thumbs.db`,
`desktop.ini`), extended attributes (from AppleDouble files and TAR pax
records, including the macOS **quarantine record** and **"where from"
URLs**: where a file was downloaded from), names that reveal a user folder
(`home/<name>/...`, `Users/<name>/...`), link targets, and hidden data
(before, between or after the entries, in unused header bytes or padding,
after the end of the archive or of the compressed data, and in TAR earlier
copies of a file that a later one with the same name replaces). **The files
inside** are read too, with the same readers metaex uses for those formats
on their own, up to 3 levels of archives inside archives and 1000 files.

**What strip does**: it writes a new archive in which every file is kept,
in the same order, with the same name and contents, and gets neutral
values: owner 0 with no names, the time 1980-01-01 00:00, permissions 644
(755 for folders and for files that were executable). Everything listed
above is removed, including the operating system files (each is listed in
the summary). A compressed file is compressed again only if what it holds
changed.

**`--deep`**: without it, the files inside are not changed; if they contain
metadata, `strip` still writes the archive, names them in a warning, and
both `strip` and `verify` say NOT CLEAN ("use --deep"). With `--deep`,
each file inside is cleaned by its own format's rules (`--paranoid` and
the other options apply to them as well) and checked against the original
file inside. Files metaex can't clean (other formats, damaged files,
archives nested deeper than 3 levels) are copied unchanged and listed,
never dropped.

**Files metaex could not examine** (past the 1000 files or 3 levels of
nesting, of a format metaex doesn't read, or damaged) may still hold
metadata: show and risk warn about them, and `strip` and `verify` list them
as "not checked" and say NOT CLEAN. Files whose type isn't recognized at
all (such as plain text) are content, not metadata, and don't count.

**verify** checks that no metadata is left in the archive (or, with
`--deep` output, in the files inside), that the entries are the same (names,
types, link targets, order), and that every file inside is byte for byte
the original or a cleaned copy that verifies against it.

**Refused in every archive**: damaged or truncated archives, names that
would be extracted outside the folder (`../`, absolute paths), links that
point to an absolute path, outside the archive or into a user folder, and
archives that would expand to more than 1 GiB or more than 200 times their
size ("zip bombs"). One decompression budget is shared by everything read
from a file.

**Known limits for all archives**: **file names are kept** (a name can
reveal a person, a project or a user folder; metaex warns about user
folders). The order of the files is kept. Without `--deep` the files
inside keep their metadata; with it, formats metaex doesn't read (PDF,
plain text, ...) are copied as they are.

### ZIP

Extension: `.zip`.

- **strip removes** entry times, comments, extra fields, operating system
  files and hidden data. Entries keep their compressed bytes as they are.
- **Refused**: ZIP archives with encrypted entries, and signed archives
  (cleaning would break the signature), including Java and Android
  packages (see [Refused formats](#refused-formats)).
- **Known limits**: a cleaned ZIP says it was made on Unix (where
  permissions are stored), whatever the original said. Compressed data is
  copied as it is, so the compression settings of the original program
  stay visible.

### TAR

Extension: `.tar`.

- **strip removes** entry times, owners and their names, pax records other
  than long names, extended attributes, operating system files, earlier
  copies of a replaced file and hidden data. The archive is written as a
  plain ustar archive.
- **Refused**: TAR archives with sparse files, bad header checksums.

### gzip, bzip2 and xz

Extensions: `.gz`, `.tgz`; `.bz2`, `.tbz2`, `.tbz`; `.xz`, `.txz`. These
are compressed TAR archives (`.tar.gz`, `.tar.bz2`, `.tar.xz`) or single
compressed files (`photo.jpg.gz`); what they hold is handled by its own
format (a TAR archive inside is cleaned as above).

- **Reads**: for gzip, the header (original file name, modification time,
  operating system, comment, extra fields); for all three, data after the
  compressed data.
- **strip removes** the gzip header's name, time and comment (a new header
  is written) and hidden data after the compressed data. The data is
  compressed again only if what it holds changed.
- **Refused**: damaged compressed data, xz files with anything but padding
  after their data, decompression bombs.

## Refused formats

metaex recognizes these by their content. `show` and `risk` list only
their file system information; `strip` and `verify` refuse them and write
nothing. `metaex formats` gives the same list.

| Format | Extensions | Why |
|--------|------------|-----|
| TIFF | `.tif`, `.tiff` | not supported yet |
| HEIC | `.heic`, `.heif`, `.hif` | not supported yet |
| HEIF | `.heif`, `.heic`, `.hif` | not supported yet |
| AVIF | `.avif` | not supported yet |
| PDF | `.pdf` | not supported yet |
| EPUB | `.epub` | ZIP archives with rules of their own (and metadata in their package files) |
| Java archive (JAR, WAR, EAR) | `.jar`, `.war`, `.ear` | often signed; any change would break the signature |
| Android package (APK) | `.apk`, `.aab` | signed; any change would break the signature |
| Legacy Office (OLE2) | `.doc`, `.xls`, `.ppt`, ... | save them in the current format (.docx, .xlsx, .pptx) first |
| Word 97-2003 document (.doc) | `.doc`, `.dot` | save as .docx first |
| Excel 97-2003 workbook (.xls) | `.xls`, `.xlt` | save as .xlsx first |
| PowerPoint 97-2003 presentation (.ppt) | `.ppt`, `.pot`, `.pps` | save as .pptx first |
| Password-protected Office document | `.docx`, `.xlsx`, `.pptx`, ... | encrypted; remove the password first |
| Ogg | `.ogg`, `.oga`, `.opus`, ... | not supported (Vorbis, Opus, FLAC in Ogg) |
| RF64/BW64 (WAV over 4 GiB) | `.wav`, `.rf64`, `.bw64` | WAV files over 4 GiB are not supported |
| AIFF | `.aif`, `.aiff`, `.aifc` | not supported; convert to WAV or FLAC |
| MP4 | `.mp4`, `.m4v` | not supported yet |
| QuickTime | `.mov`, `.qt` | not supported yet |
| M4A | `.m4a`, `.m4b`, `.m4p` | M4A/AAC audio is not supported |
| 7-Zip | `.7z` | not supported |
| RAR | `.rar` | not supported |

Also refused: password-protected OpenDocument files, "Strict Open XML"
documents and binary Excel workbooks (`.xlsb`), AAC audio in a file named
`.mp3` and "free-format" MP3, signed ZIP archives and ZIP archives with
encrypted entries, and TAR archives with sparse files. Files of any other
type are refused the same way.
