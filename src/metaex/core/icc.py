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


KNOWN_TAGS = frozenset([
    # ICC.1 (v4) and the v2 tags that v4 dropped
    "A2B0", "A2B1", "A2B2", "A2B3", "A2M0", "B2A0", "B2A1", "B2A2", "B2A3", "B2D0", "B2D1",
    "B2D2", "B2D3", "D2B0", "D2B1", "D2B2", "D2B3", "bXYZ", "bTRC", "bfd ", "bkpt", "calt",
    "chad", "chrm", "cicp", "ciis", "clot", "clro", "clrt", "cprt", "crdi", "desc", "devs",
    "dmdd", "dmnd", "gamt", "gTRC", "gXYZ", "kTRC", "lumi", "meas", "ncl2", "ncol", "pre0",
    "pre1", "pre2", "ps2i", "ps2s", "ps2r", "ps2c", "psd0", "psd1", "psd2", "psd3", "pseq",
    "psid", "resp", "rig0", "rig2", "rTRC", "rXYZ", "scrd", "scrn", "targ", "tech", "vued",
    "view", "wtpt",
    # written by operating systems: video card gamma, Apple display data, Windows HDR
    "vcgt", "dscm", "mmod", "ndin", "MHC2",
])
"""Tags of color profiles. The ICC "meta" dictionary tag and private tags
can hold anything, so a profile with them is not kept (see profile_problem())."""


def profile_problem(profile: bytes) -> str | None:
    """Why a profile may hold data that is not part of the color profile,
    or None. A cleaned file keeps an ICC profile byte for byte, so it is
    only kept if every tag is a known color tag, every byte belongs to the
    header, the tag table or a tag (bytes between tags must be zero) and the
    reserved header bytes are zero."""
    n = len(profile)
    if n < 132:
        return f"the profile is too short ({n} bytes)"
    declared = int.from_bytes(profile[0:4], "big")
    if declared != n:
        return f"the header says {declared} bytes, but the profile has {n}"
    if profile[36:40] != b"acsp":
        return "no 'acsp' signature"
    if any(profile[100:128]):
        return "the reserved bytes of the header are not zero"
    count = int.from_bytes(profile[128:132], "big")
    table_end = 132 + 12 * count
    if count > MAX_TAGS or table_end > n:
        return f"the tag table ({count} tags) does not fit in the profile"
    ranges = [(0, table_end)]
    for i in range(count):
        entry = profile[132 + 12 * i : 144 + 12 * i]
        sig = entry[0:4].decode("latin-1")
        offset, size = int.from_bytes(entry[4:8], "big"), int.from_bytes(entry[8:12], "big")
        if sig not in KNOWN_TAGS:
            return f"it has a tag metaex doesn't know ({fourcc(entry[0:4])!r})"
        if offset < table_end or offset > n or size > n - offset:
            return f"the {sig!r} tag is outside the profile"
        ranges.append((offset, offset + size))
    position = 0
    for start, end in sorted(ranges):
        if start > position and any(profile[position:start]):
            return f"{start - position} bytes at offset {position} belong to no tag"
        position = max(position, end)
    if position < n and any(profile[position:]):
        return f"{n - position} bytes at the end belong to no tag"
    return None


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

    problem = profile_problem(profile)
    if problem is not None:
        section.add("unchecked_data", "Data metaex can't check", problem, sensitive=True)
        warnings.append(f"the ICC profile may hold data that is not part of it ({problem}); "
                        "strip removes the profile")
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


# Header device fields that name a color standard rather than a device.
GENERIC_DEVICES = {("IEC", "sRGB")}


def named_device(section: Section) -> str | None:
    """"<manufacturer> <model>" if an ICC profile section names a device, else None.

    Standard profiles that only name a color standard (IEC sRGB) don't count.
    """
    values = []
    for key in ("device_manufacturer", "device_model"):
        f = section.get(key)
        if f is not None and isinstance(f.value, str) and f.value != "(none)":
            values.append(f.value)
        else:
            values.append(None)
    if tuple(values) in GENERIC_DEVICES:
        return None
    named = [v for v in values if v]
    return " ".join(named) if named else None
