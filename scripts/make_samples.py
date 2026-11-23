"""Write the sample files in tests/samples/, or check that they are up to date.

Usage (from anywhere inside the repository):

    python scripts/make_samples.py          # write the samples
    python scripts/make_samples.py --check  # fail if the files differ from the generator

Every sample is built in code with the test helpers (tests/helpers), never
with Pillow, and holds made-up metadata only. The output is the same bytes
on every run and every machine: fixed values, no clock, no randomness, and
PNG data in stored deflate blocks (zlib's compressed output differs between
zlib builds).

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

from helpers import jpeg, png, tiff  # noqa: E402
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
            png.text("Comment", "Contact: jane.doe@example.com"),
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
