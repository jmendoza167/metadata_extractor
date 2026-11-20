"""ICC color profiles: header fields and descriptive text tags.

Used by JPEG (APP2 ICC_PROFILE) and later PNG (iCCP) and WebP (ICCP).
"""

from __future__ import annotations

from datetime import datetime

from metaex.core.errors import MalformedFileError
from metaex.core.reader import ByteReader
from metaex.core.result import Section

MAX_ICC_SIZE = 64 * 1024 * 1024
MAX_TAGS = 1000
MAX_TEXT = 4096

DEVICE_CLASSES = {
    "scnr": "Input device", "mntr": "Display device", "prtr": "Output device",
    "link": "Device link", "spac": "Color space", "abst": "Abstract", "nmcl": "Named color",
}
PLATFORMS = {"APPL": "Apple", "MSFT": "Microsoft", "SGI": "Silicon Graphics",
             "SUNW": "Sun Microsystems", "TGNT": "Taligent"}
INTENTS = {0: "Perceptual", 1: "Relative colorimetric", 2: "Saturation",
           3: "Absolute colorimetric"}
TEXT_TAGS = (("desc", "description", "Description"),
             ("cprt", "copyright", "Copyright"),
             ("dmnd", "device_manufacturer_description", "Device manufacturer description"),
             ("dmdd", "device_model_description", "Device model description"))


def fourcc(raw: bytes) -> str:
    """A 4-byte signature as text; "(none)" if zero, hex if not printable."""
    if raw == b"\x00\x00\x00\x00":
        return "(none)"
    if all(0x20 <= b < 0x7F for b in raw):
        return raw.decode("ascii").strip() or "(none)"
    return "0x" + raw.hex()


def icc_section(profile: bytes, warnings: list[str]) -> Section:
    """Header fields and text tags of an ICC profile.

    Raises MalformedFileError if the 128-byte header is missing.
    """
    if len(profile) > MAX_ICC_SIZE:
        raise MalformedFileError(f"ICC profile is larger than {MAX_ICC_SIZE} bytes")
    r = ByteReader(profile, "big")
    if len(profile) < 128:
        raise MalformedFileError(f"ICC profile is too short ({len(profile)} bytes)")
    section = Section("icc", "ICC profile")
    declared = r.u32("profile size")
    section.add("size", "Size", f"{len(profile)} bytes")
    if declared != len(profile):
        warnings.append(f"ICC profile header says {declared} bytes, but {len(profile)} "
                        "were found")
    section.add("cmm", "Preferred CMM", fourcc(r.read(4)))
    major, minor_bugfix = r.u8(), r.u8()
    r.skip(2)
    section.add("version", "Version", f"{major}.{minor_bugfix >> 4}.{minor_bugfix & 0xF}")
    device_class = fourcc(r.read(4))
    section.add("device_class", "Device class", DEVICE_CLASSES.get(device_class, device_class))
    section.add("color_space", "Color space", fourcc(r.read(4)))
    section.add("pcs", "Connection space", fourcc(r.read(4)))
    stamp = [r.u16() for _ in range(6)]
    try:
        created = datetime(*stamp).isoformat()
    except ValueError:
        created = "invalid date " + "-".join(str(v) for v in stamp)
    section.add("created", "Profile created", created)
    if r.read(4) != b"acsp":
        warnings.append("ICC profile has no 'acsp' signature; it may not be a valid profile")
    platform = fourcc(r.read(4))
    section.add("platform", "Primary platform", PLATFORMS.get(platform, platform))
    r.skip(4)  # flags
    manufacturer = fourcc(r.read(4))
    model = fourcc(r.read(4))
    section.add("device_manufacturer", "Device manufacturer", manufacturer,
                sensitive=manufacturer != "(none)")
    section.add("device_model", "Device model", model, sensitive=model != "(none)")
    r.skip(8)  # attributes
    intent = r.u32()
    section.add("rendering_intent", "Rendering intent", INTENTS.get(intent, str(intent)))
    r.skip(12)  # illuminant
    section.add("creator", "Profile creator", fourcc(r.read(4)))
    profile_id = r.read(16)
    section.add("profile_id", "Profile ID",
                profile_id.hex() if any(profile_id) else "(not set)")

    tags = _tag_table(r, warnings)
    for sig, key, label in TEXT_TAGS:
        if sig in tags:
            offset, size = tags[sig]
            text = _text_tag(profile, offset, size)
            if text is not None:
                section.add(key, label, text)
    return section


def _tag_table(r: ByteReader, warnings: list[str]) -> dict[str, tuple[int, int]]:
    tags: dict[str, tuple[int, int]] = {}
    try:
        r.seek(128)
        count = r.u32("ICC tag count")
        if count > MAX_TAGS:
            warnings.append(f"ICC profile claims {count} tags; only {MAX_TAGS} were read")
            count = MAX_TAGS
        for _ in range(count):
            sig = r.read(4, "ICC tag signature").decode("latin-1")
            offset, size = r.u32("ICC tag offset"), r.u32("ICC tag size")
            tags.setdefault(sig, (offset, size))
    except MalformedFileError as exc:
        warnings.append(f"ICC tag table is truncated: {exc}")
    return tags


def _text_tag(profile: bytes, offset: int, size: int) -> str | None:
    """Decode a desc / mluc / text tag; None if it is out of bounds or unknown."""
    if offset > len(profile) or size > len(profile) - offset or size < 12:
        return None
    tag = ByteReader(profile[offset : offset + size], "big")
    try:
        kind = tag.read(4)
        tag.skip(4)
        if kind == b"desc":
            n = tag.u32()
            raw = tag.read(min(n, tag.remaining, MAX_TEXT))
            return raw.split(b"\x00", 1)[0].decode("latin-1").strip()
        if kind == b"text":
            raw = tag.read(min(tag.remaining, MAX_TEXT))
            return raw.split(b"\x00", 1)[0].decode("latin-1").strip()
        if kind == b"mluc":
            records, record_size = tag.u32(), tag.u32()
            if records == 0 or record_size < 12:
                return None
            tag.seek(16)
            tag.skip(4)  # language + country of the first record
            length, start = tag.u32(), tag.u32()
            text = tag.sub(start, min(length, MAX_TEXT * 2)).read(min(length, MAX_TEXT * 2))
            return text[: len(text) - len(text) % 2].decode("utf-16-be", errors="replace").strip()
    except MalformedFileError:
        return None
    return None
