"""Write the sample files in tests/samples/, or check that they are up to date.

Usage (from anywhere inside the repository):

    python scripts/make_samples.py          # write the samples
    python scripts/make_samples.py --check  # fail if the files differ from the generator

Every sample is built in code with the test helpers (tests/helpers), never
with Pillow, and holds made-up metadata only. The output is the same bytes
on every run and every machine: fixed values, no clock, no randomness,
PNG data in stored deflate blocks, Office files with STORED ZIP entries
(zlib's compressed output differs between zlib builds), and a hand-written
deflate stream for the zip bomb.

The generator owns everything in tests/samples/ except SAMPLES.md,
expected/ (the snapshots, written by the tests) and real/ (files from real
software, added by hand). --check fails if a sample is missing or differs,
or if there is any other file it doesn't make.

Development tool only: uses the standard library and the test helpers.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "tests" / "samples"
NOT_GENERATED = ("SAMPLES.md", "expected", "real")
"""Entries of tests/samples/ the generator never writes or checks."""

MAX_FILE_SIZE = 100 * 1024
MAX_TOTAL_SIZE = 1024 * 1024

if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from helpers import gif, jpeg, ooxml, png, svg, tiff, webp, zip  # noqa: E402
from helpers.deflate import repeat_deflate  # noqa: E402
from helpers.jpeg_encoder import jpeg_file  # noqa: E402

Builder = Callable[[], dict[str, bytes]]
BUILDERS: list[Builder] = []


def sample_set(builder: Builder) -> Builder:
    """Register a function returning {relative path: bytes} for some samples."""
    BUILDERS.append(builder)
    return builder


# -- samples ----------------------------------------------------------------------------
# All values are made up: Jane Doe, @example.com, GPS at the Eiffel Tower
# (48.8584 N, 2.2945 E), serial numbers like SN-0001.

JFIF = jpeg.app(0, b"JFIF\x00\x01\x02\x01\x00\x48\x00\x48\x00\x00")

PNG_WIDTH, PNG_HEIGHT = 16, 12
"""Size of every PNG sample; the image is helpers.png.raw_image()."""
APNG_FRAMES = 3
"""Frames in png/apng_metadata.png; frame n > 0 is raw_image(seed=10 + n)."""


@sample_set
def jpeg_samples() -> dict[str, bytes]:
    orientation_only = tiff.build_tiff([(0x0112, tiff.SHORT, 6)])
    return {
        # a real 48x32 baseline image with a JFIF header and nothing else
        "jpeg/clean.jpg": jpeg_file(JFIF),
        # like a phone photo: camera EXIF, GPS, thumbnail, XMP
        "jpeg/gps_camera.jpg": jpeg.sample_photo(),
        # every kind of JPEG metadata metaex reads
        "jpeg/kitchen_sink.jpg": jpeg.kitchen_sink_jpeg(),
        # EXIF with only Orientation 6: shown rotated 90 degrees clockwise
        "jpeg/orientation_6.jpg": jpeg_file(JFIF, jpeg.app(1, tiff.exif_payload(orientation_only))),
    }


@sample_set
def png_samples() -> dict[str, bytes]:
    from helpers.jpeg import SAMPLE_XMP

    size = {"width": PNG_WIDTH, "height": PNG_HEIGHT}
    return {
        "png/clean.png": png.make_png(**size),
        "png/text_chunks.png": png.make_png(
            png.text("Author", "Jane Doe"),
            png.text("Comment", "Write to jane.doe@example.com for the plans"),
            png.ztxt("Description", "Floor plan of the flat, second draft"),
            png.itxt("Title", "Plan de l'appartement", language="fr",
                     translated="Floor plan", compress=True),
            png.itxt("Software", "Editor 2.0"),
            **size),
        "png/exif_time.png": png.make_png(
            png.time_chunk(2024, 5, 1, 12, 30, 0),
            before_idat=(png.exif_chunk(png.kitchen_sink_tiff()),), **size),
        "png/apng_metadata.png": png.apng(
            png.text("Author", "Jane Doe"),
            png.time_chunk(2024, 5, 1, 12, 30, 0),
            png.itxt("XML:com.adobe.xmp", SAMPLE_XMP),
            frames=APNG_FRAMES, **size),
    }


@sample_set
def gif_samples() -> dict[str, bytes]:
    from helpers.jpeg import SAMPLE_XMP

    frames = []
    for i in range(3):
        frames.append(gif.graphic_control(delay=20 + 10 * i, disposal=1))
        frames.append(gif.image(12, 10, seed=i + 1,
                                local_table=gif.palette(4, seed=60) if i == 1 else None))
    return {
        "gif/clean.gif": gif.make_gif(gif.image(12, 10), width=12, height=10),
        "gif/animated_metadata.gif": gif.make_gif(
            gif.comment("Made by Jane Doe (jane.doe@example.com) with Editor 2.0"),
            gif.loop(0),
            gif.xmp_extension(SAMPLE_XMP),
            *frames,
            gif.application(b"ImageMagick", b"gamma=0.45455"),
            gif.comment("Second draft, 2024-05-01"),
            width=12, height=10),
    }


@sample_set
def webp_samples() -> dict[str, bytes]:
    from helpers.jpeg import SAMPLE_XMP, icc_profile

    camera_exif = png.kitchen_sink_tiff()
    return {
        # the 1x1 lossy image is Google's WebP feature-detection test image
        "webp/lossy_metadata.webp": webp.extended(
            webp.iccp(icc_profile()), webp.chunk("VP8 ", webp.VP8_1X1),
            webp.exif_chunk(camera_exif), webp.xmp_chunk(SAMPLE_XMP)),
        "webp/lossless.webp": webp.lossless(16, 12, green2=60),
        "webp/animated_metadata.webp": webp.animated(
            webp.exif_chunk(camera_exif), webp.xmp_chunk(SAMPLE_XMP)),
    }


EDITOR_SVG = f"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!-- Created with Inkscape (http://www.inkscape.org/) -->

<svg
   width="210mm"
   height="148mm"
   viewBox="0 0 210 148"
   version="1.1"
   id="svg1"
   inkscape:version="1.3.2 (091e20e, 2023-11-25)"
   sodipodi:docname="floor plan.svg"
   inkscape:export-filename="C:\\Users\\jane.doe\\Pictures\\floor plan.png"
   inkscape:export-xdpi="96"
   inkscape:export-ydpi="96"
   xmlns:inkscape="{svg.INKSCAPE_NS}"
   xmlns:sodipodi="{svg.SODIPODI_NS}"
   xmlns="{svg.SVG_NS}"
   xmlns:svg="{svg.SVG_NS}"
   xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
   xmlns:cc="http://creativecommons.org/ns#"
   xmlns:dc="http://purl.org/dc/elements/1.1/">
  <title
     id="title1">Floor plan</title>
  <sodipodi:namedview
     id="namedview1"
     pagecolor="#ffffff"
     bordercolor="#666666"
     inkscape:zoom="0.7"
     inkscape:window-width="1920"
     inkscape:window-height="1016"
     inkscape:current-layer="layer1" />
  <metadata
     id="metadata1">
    <rdf:RDF>
      <cc:Work
         rdf:about="">
        <dc:format>image/svg+xml</dc:format>
        <dc:title>Floor plan</dc:title>
        <dc:creator>
          <cc:Agent>
            <dc:title>Jane Doe &lt;jane.doe@example.com&gt;</dc:title>
          </cc:Agent>
        </dc:creator>
        <dc:date>2024-05-01</dc:date>
      </cc:Work>
    </rdf:RDF>
  </metadata>
  <g
     inkscape:label="Layer 1"
     inkscape:groupmode="layer"
     id="layer1">
    <!-- ask the landlord about the stairs -->
    <rect
       style="fill:#d0e0f0;stroke:#000000;stroke-width:1"
       id="rect1"
       width="120"
       height="80"
       x="20"
       y="30" />
    <path
       sodipodi:nodetypes="cc"
       d="M 80,30 V 110"
       style="stroke:#000000;stroke-width:1"
       id="path1" />
    <text
       xml:space="preserve"
       x="30"
       y="50"
       id="text1"><tspan
         sodipodi:role="line"
         id="tspan1">Kitchen &amp; dining</tspan></text>
  </g>
</svg>
"""

SVG11_DOCTYPE = ('<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
                 '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n')

DRAWING = ('\n  <rect x="10" y="10" width="60" height="40" fill="#3070b0"/>'
           '\n  <circle cx="70" cy="50" r="20" fill="#e0a020"/>\n')


@sample_set
def svg_samples() -> dict[str, bytes]:
    declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    photo = svg.data_uri(jpeg.sample_photo(), "image/jpeg")
    return {
        "svg/clean.svg": svg.simple_svg(DRAWING, prolog=declaration),
        "svg/editor.svg": EDITOR_SVG.encode("utf-8"),
        "svg/doctype_svg11.svg": svg.simple_svg(
            "\n  <title>Logo</title>" + DRAWING, prolog=declaration + SVG11_DOCTYPE),
        "svg/embedded_jpeg.svg": svg.simple_svg(
            f'\n  <image x="0" y="0" width="48" height="32" href="{photo}"/>\n',
            prolog=declaration),
    }


@sample_set
def malformed_samples() -> dict[str, bytes]:
    """Damaged files: metaex must refuse to strip them, and write nothing."""
    photo = jpeg.sample_photo()
    scan_start, _ = jpeg.scan_range(photo)
    comment = gif.comment("Jane Doe")
    good_gif = gif.make_gif(comment, gif.image(8, 8), width=8, height=8)
    at = good_gif.index(comment) + 2  # the comment's first sub-block size byte
    lossless = webp.lossless(8, 8)
    riff_size = int.from_bytes(lossless[4:8], "little")
    return {
        # cut off in the middle of the image data, like an interrupted download
        "malformed/truncated.jpg": photo[: scan_start + 40],
        # the tEXt chunk's CRC doesn't match its contents
        "malformed/bad_crc.png": png.make_png(
            png.chunk("tEXt", b"Author\x00Jane Doe", crc=0x12345678),
            width=PNG_WIDTH, height=PNG_HEIGHT),
        # the comment's sub-block says 255 bytes, more than the rest of the file
        "malformed/broken_subblock.gif": good_gif[:at] + b"\xff" + good_gif[at + 1:],
        # the RIFF header says the file is 100 bytes longer than it is
        "malformed/wrong_riff_size.webp": (lossless[:4] + (riff_size + 100).to_bytes(4, "little")
                                           + lossless[8:]),
        # an XML entity declaration (refused: entities enable XML attacks)
        "malformed/entity.svg": (b'<?xml version="1.0" encoding="UTF-8"?>\n'
                                 b'<!DOCTYPE svg [<!ENTITY author "Jane Doe">]>\n'
                                 b'<svg xmlns="http://www.w3.org/2000/svg" width="10" '
                                 b'height="10"><title>&author;</title></svg>\n'),
        **malformed_office(),
    }


@sample_set
def office_samples() -> dict[str, bytes]:
    """Every ZIP entry is STORED with the 1980-01-01 time, so the bytes are the
    same on every machine (no zlib involved)."""
    return {
        "docx/clean.docx": ooxml.docx(ooxml.paragraph("Hello")).build(),
        "docx/kitchen_sink.docx": ooxml.kitchen_sink_docx(),
        "docx/macro.docm": ooxml.macro_docm(),
        "xlsx/clean.xlsx": ooxml.xlsx().build(),
        "xlsx/kitchen_sink.xlsx": ooxml.kitchen_sink_xlsx(),
        "pptx/clean.pptx": ooxml.pptx().build(),
        "pptx/kitchen_sink.pptx": ooxml.kitchen_sink_pptx(),
    }


BOMB_MATCHES = 8200
"""word/document.xml of malformed/zip_bomb.docx: 2,065 bytes that expand to
about 2 MiB of spaces (a ratio of about 1000, over the limit of 200)."""


def malformed_office() -> dict[str, bytes]:
    files = ooxml.docx().files()
    raw, expanded = repeat_deflate(0x20, BOMB_MATCHES)

    def entries(**changes) -> list[zip.Entry]:
        out = []
        for name, data in files.items():
            entry = changes.get(name, zip.Entry(name, data))
            out.append(entry)
        return out + changes.get("extra", [])

    kitchen = ooxml.kitchen_sink_docx()
    return {
        # word/document.xml expands to over 200 times its size (tiny when expanded)
        "malformed/zip_bomb.docx": zip.build_zip(entries(**{
            "word/document.xml": zip.Entry("word/document.xml", expanded, method=zip.DEFLATED,
                                           compressed=raw)})),
        # an entry named ../evil.txt: extracting it would write outside the folder
        "malformed/path_traversal.docx": zip.build_zip(entries(extra=[
            zip.Entry("word/../../evil.txt", b"written outside the folder\n")])),
        # word/document.xml is marked as encrypted (password-protected entry)
        "malformed/encrypted_entry.docx": zip.build_zip(entries(**{
            "word/document.xml": zip.Entry("word/document.xml", files["word/document.xml"],
                                           flags=zip.FLAG_ENCRYPTED)})),
        # docx/kitchen_sink.docx cut in half, like an interrupted download
        "malformed/truncated.docx": kitchen[: len(kitchen) // 2],
    }


# -- building ---------------------------------------------------------------------------


def build() -> dict[str, bytes]:
    """Every sample, as {relative POSIX path: bytes}, sorted by path."""
    samples: dict[str, bytes] = {}
    with png.stored_deflate():
        for builder in BUILDERS:
            for name, data in builder().items():
                if name in samples:
                    raise ValueError(f"two samples are named {name}")
                if name.split("/", 1)[0] in NOT_GENERATED or "/" not in name:
                    raise ValueError(f"{name}: samples go in a format folder")
                samples[name] = data
    check_sizes(samples)
    return dict(sorted(samples.items()))


def check_sizes(samples: dict[str, bytes]) -> None:
    for name, data in samples.items():
        if len(data) >= MAX_FILE_SIZE:
            raise ValueError(f"{name} is {len(data)} bytes; samples must stay under "
                             f"{MAX_FILE_SIZE} bytes")
    total = sum(len(d) for d in samples.values())
    if total >= MAX_TOTAL_SIZE:
        raise ValueError(f"the samples add up to {total} bytes; they must stay under "
                         f"{MAX_TOTAL_SIZE} bytes")


# -- files on disk ----------------------------------------------------------------------


def files_on_disk(root: Path) -> dict[str, Path]:
    """Every file under ``root`` the generator owns, by relative POSIX path."""
    if not root.is_dir():
        return {}
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts[0] in NOT_GENERATED or not path.is_file():
            continue
        out[rel.as_posix()] = path
    return out


def check(samples: dict[str, bytes], root: Path = SAMPLES_DIR) -> list[str]:
    """Problems found comparing ``samples`` with the files under ``root``."""
    on_disk = files_on_disk(root)
    problems = []
    for name, data in samples.items():
        path = on_disk.get(name)
        if path is None:
            problems.append(f"missing: {name}")
        elif path.read_bytes() != data:
            problems.append(f"differs from the generator: {name}")
    for name in on_disk:
        if name not in samples:
            problems.append(f"not made by the generator: {name}")
    return problems


def write(samples: dict[str, bytes], root: Path = SAMPLES_DIR) -> tuple[list[str], list[str]]:
    """Write the samples that are missing or differ. Returns (written,
    extra): files not made by the generator are listed, never deleted."""
    written = []
    on_disk = files_on_disk(root)
    for name, data in samples.items():
        path = root / name
        if name in on_disk and path.read_bytes() == data:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(name)
    extra = [name for name in on_disk if name not in samples]
    return written, extra


# -- command line -----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None, root: Path = SAMPLES_DIR) -> int:
    parser = argparse.ArgumentParser(description="Write or check the files in tests/samples/.")
    parser.add_argument("--check", action="store_true",
                        help="write nothing; fail if any sample is missing, differs or "
                             "is not made by the generator")
    args = parser.parse_args(argv)
    samples = build()
    if args.check:
        problems = check(samples, root)
        for problem in problems:
            print(f"make_samples: {problem}", file=sys.stderr)
        if problems:
            print("make_samples: run python scripts/make_samples.py to update the samples",
                  file=sys.stderr)
            return 1
        print(f"make_samples: all {len(samples)} sample files match the generator")
        return 0
    written, extra = write(samples, root)
    print(f"make_samples: {len(written)} of {len(samples)} sample files written")
    for name in written:
        print(f"  {name}")
    for name in extra:
        print(f"make_samples: not made by the generator (remove it by hand): {name}",
              file=sys.stderr)
    return 1 if extra else 0


if __name__ == "__main__":
    sys.exit(main())

