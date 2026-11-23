"""Decode images with Pillow, an independent decoder, for pixel checks.

Pillow is a test-only dependency: metaex never imports it. It is imported
here directly (not with pytest.importorskip), so a missing Pillow makes
the tests fail instead of silently skipping them.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass

from PIL import Image, ImageFile, ImageOps, ImageSequence

MAX_FRAMES = 1000


@dataclass(frozen=True)
class Frame:
    mode: str
    size: tuple[int, int]
    pixels: bytes
    """Raw pixel bytes in ``mode`` (Image.tobytes())."""
    duration: int | None
    """Display time in milliseconds for animation frames, else None."""


def _read(source: bytes | bytearray | str | os.PathLike[str]) -> bytes:
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source)
    with open(source, "rb") as f:
        return f.read()


def open_image(source: bytes | bytearray | str | os.PathLike[str]) -> Image.Image:
    """The image opened with Pillow (not decoded yet)."""
    if ImageFile.LOAD_TRUNCATED_IMAGES:
        raise RuntimeError("Pillow is set to accept truncated images; pixel checks need "
                           "strict decoding")
    return Image.open(io.BytesIO(_read(source)))


def decode_frames(source: bytes | bytearray | str | os.PathLike[str], *,
                  transpose: bool = False) -> list[Frame]:
    """Every frame of the image, fully decoded by Pillow, as it is displayed.

    Animated GIF, APNG and animated WebP give one Frame per animation frame
    (composited onto the canvas, as Pillow shows them); still images give one.
    With ``transpose``, each frame is first turned the way its EXIF
    Orientation says (ImageOps.exif_transpose), i.e. as a viewer shows it.
    Raises whatever Pillow raises for an image it can't decode.
    """
    image = open_image(source)
    animated = getattr(image, "is_animated", False)
    frames = []
    for n, frame in enumerate(ImageSequence.Iterator(image)):
        if n >= MAX_FRAMES:
            raise ValueError(f"more than {MAX_FRAMES} frames")
        frame.load()
        shown = ImageOps.exif_transpose(frame) if transpose else frame
        duration = frame.info.get("duration") if animated else None
        frames.append(Frame(shown.mode, shown.size, shown.tobytes(),
                            None if duration is None else int(duration)))
    return frames


def loop_count(source: bytes | bytearray | str | os.PathLike[str]) -> int | None:
    """How often an animation repeats (0 = forever), or None if it doesn't say."""
    loop = open_image(source).info.get("loop")
    return None if loop is None else int(loop)
