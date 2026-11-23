"""Build small, structurally valid JPEG files for tests.

make_jpeg() writes placeholder images: they only have the marker structure
metaex reads and don't decode to a real picture. sample_photo(),
sample_photo_rotated() and kitchen_sink_jpeg() hold a real baseline image
(helpers.jpeg_encoder), so their pixels can be checked with a decoder.
"""

from __future__ import annotations

import struct

from helpers.jpeg_encoder import jpeg_file

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"

# Entropy-coded data containing a stuffed 0xFF byte (FF00).
SCAN_DATA = b"\x12\x34\xff\x00\x56\x78"

PHOTO_WIDTH, PHOTO_HEIGHT = 48, 32
"""Size of the real image in sample_photo() and kitchen_sink_jpeg()."""


def segment(marker: int, payload: bytes) -> bytes:
    """A marker segment with a correct length field."""
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def app(n: int, payload: bytes) -> bytes:
    return segment(0xE0 + n, payload)


def sof0(width: int = 8, height: int = 8) -> bytes:
    return segment(0xC0, struct.pack(">BHHB", 8, height, width, 1) + b"\x01\x11\x00")


def dqt() -> bytes:
    return segment(0xDB, b"\x00" + bytes(64))


def dht() -> bytes:
    return segment(0xC4, b"\x00" + bytes(16))


def sos() -> bytes:
    return segment(0xDA, b"\x01\x01\x00\x00\x3f\x00")


XMP_ID = b"http://ns.adobe.com/xap/1.0/\x00"
XMP_EXTENSION_ID = b"http://ns.adobe.com/xmp/extension/\x00"
PHOTOSHOP_ID = b"Photoshop 3.0\x00"


def xmp_segment(xml) -> bytes:
    data = xml.encode("utf-8") if isinstance(xml, str) else xml
    return app(1, XMP_ID + data)


def extended_xmp_segments(xml, guid: str = "0123456789ABCDEF0123456789ABCDEF",
                          chunk_size: int = 100, total=None) -> list[bytes]:
    data = xml.encode("utf-8") if isinstance(xml, str) else xml
    total = len(data) if total is None else total
    out = []
    for offset in range(0, len(data), chunk_size):
        chunk = data[offset : offset + chunk_size]
        out.append(app(1, XMP_EXTENSION_ID + guid.encode("ascii")
                       + struct.pack(">II", total, offset) + chunk))
    return out


def photoshop_resource(rid: int, data: bytes, name: bytes = b"") -> bytes:
    pascal = bytes([len(name)]) + name
    if len(pascal) % 2:
        pascal += b"\x00"
    out = b"8BIM" + struct.pack(">H", rid) + pascal + struct.pack(">I", len(data)) + data
    return out + (b"\x00" if len(data) % 2 else b"")


def iim(*datasets) -> bytes:
    """IPTC-IIM datasets from (record, dataset, value) tuples."""
    out = b""
    for record, dataset, value in datasets:
        value = value.encode("utf-8") if isinstance(value, str) else value
        out += b"\x1c" + bytes([record, dataset]) + struct.pack(">H", len(value)) + value
    return out


def photoshop_segment(*resources: bytes) -> bytes:
    return app(13, PHOTOSHOP_ID + b"".join(resources))


def icc_profile(description: str = "Test RGB profile", manufacturer: bytes = b"ACME",
                model: bytes = b"X100", creator: bytes = b"meta",
                copyright_text: str = "Copyright Test") -> bytes:
    """A small ICC v4 profile with a 'desc' (v2 text) and a 'cprt' (mluc) tag."""
    header = bytearray(128)
    header[4:8] = b"lcms"
    header[8], header[9] = 4, 0x30
    header[12:16], header[16:20], header[20:24] = b"mntr", b"RGB ", b"XYZ "
    struct.pack_into(">6H", header, 24, 2024, 5, 1, 12, 30, 0)
    header[36:40], header[40:44] = b"acsp", b"APPL"
    header[48:52], header[52:56] = manufacturer.ljust(4), model.ljust(4)
    header[80:84] = creator
    text = description.encode("latin-1") + b"\x00"
    desc = b"desc" + bytes(4) + struct.pack(">I", len(text)) + text
    desc += bytes(-len(desc) % 4)
    utf16 = copyright_text.encode("utf-16-be")
    cprt = b"mluc" + bytes(4) + struct.pack(">II", 1, 12) + b"enUS" + struct.pack(">II", len(utf16), 28) + utf16
    table_size = 4 + 12 * 2
    desc_offset = 128 + table_size
    cprt_offset = desc_offset + len(desc)
    table = struct.pack(">I", 2) + b"desc" + struct.pack(">II", desc_offset, len(desc))
    table += b"cprt" + struct.pack(">II", cprt_offset, len(cprt))
    profile = bytearray(header + table + desc + cprt)
    struct.pack_into(">I", profile, 0, len(profile))
    return bytes(profile)


def icc_segments(profile: bytes, chunk_size: int = 65519) -> list[bytes]:
    chunks = [profile[i : i + chunk_size] for i in range(0, len(profile), chunk_size)]
    return [app(2, b"ICC_PROFILE\x00" + bytes([i + 1, len(chunks)]) + c)
            for i, c in enumerate(chunks)]


MPF_HEADER_OFFSET = 2 + 4 + 4
"""Where MPF offsets count from when the MPF APP2 directly follows SOI:
SOI, APP2 marker + length, "MPF\\0"."""


def mpf_segment(extra_images, offsets, order: str = "<") -> bytes:
    """An APP2 MPF segment listing the primary image and the given
    (type, image_bytes) extras at ``offsets`` (relative to the MPF header).
    Its size doesn't depend on the offsets, so it can be built once with
    placeholder offsets to find out where the extra images will be."""
    from helpers.tiff import LONG, UNDEFINED, ifd_bytes, tiff_header

    bo = "<" if order == "<" else ">"
    entries = struct.pack(bo + "IIIHH", 0x20030000, 0, 0, 0, 0)
    for (kind, image), offset in zip(extra_images, offsets, strict=True):
        entries += struct.pack(bo + "IIIHH", kind, len(image), offset, 0, 0)
    ifd = [(0xB000, UNDEFINED, b"0100"), (0xB001, LONG, 1 + len(extra_images)),
           (0xB002, UNDEFINED, entries)]
    return app(2, b"MPF\x00" + tiff_header(order) + ifd_bytes(ifd, 8, order))


def with_mpf(primary: bytes, extra_images, order: str = "<") -> bytes:
    """``primary`` (a complete JPEG) with an MPF segment inserted right after
    SOI and the extra images appended after it, the offsets computed once
    every segment is in place."""
    placeholder = mpf_segment(extra_images, [0] * len(extra_images), order)
    offsets, position = [], len(placeholder) + len(primary)
    for _, image in extra_images:
        offsets.append(position - MPF_HEADER_OFFSET)
        position += len(image)
    mpf = mpf_segment(extra_images, offsets, order)
    assert len(mpf) == len(placeholder)
    return (primary[:2] + mpf + primary[2:]
            + b"".join(image for _, image in extra_images))


def make_mpf_jpeg(extra_images, order: str = "<") -> bytes:
    """A JPEG whose APP2 MPF index lists the given (type, image_bytes) extras,
    which are appended after the primary image's EOI."""
    return with_mpf(make_jpeg(), extra_images, order)


SAMPLE_XMP = """<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"
    xmlns:stEvt="http://ns.adobe.com/xap/1.0/sType/ResourceEvent#"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:exif="http://ns.adobe.com/exif/1.0/"
    xmlns:tiff="http://ns.adobe.com/tiff/1.0/"
    xmp:CreatorTool="Editor 2.0"
    xmpMM:DocumentID="xmp.did:1234"
    xmpMM:OriginalDocumentID="xmp.did:abcd"
    tiff:Orientation="1"
    exif:GPSLatitude="48,51.502N"
    exif:GPSLongitude="2,17.669E">
   <dc:creator><rdf:Seq><rdf:li>Jane Doe</rdf:li></rdf:Seq></dc:creator>
   <dc:subject><rdf:Bag><rdf:li>holiday</rdf:li><rdf:li>beach</rdf:li></rdf:Bag></dc:subject>
   <xmpMM:History>
    <rdf:Seq>
     <rdf:li stEvt:action="created" stEvt:when="2024-05-01T10:00:00+02:00"/>
     <rdf:li rdf:parseType="Resource">
      <stEvt:action>saved</stEvt:action>
      <stEvt:softwareAgent>Editor 2.0</stEvt:softwareAgent>
     </rdf:li>
    </rdf:Seq>
   </xmpMM:History>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""


def sample_photo(order: str = "<", trailing: bytes = b"", orientation: int = 1) -> bytes:
    """A JPEG like a phone photo: a real 48x32 image, EXIF with camera info,
    GPS, ``orientation`` and a thumbnail, plus XMP. All values are made up."""
    from helpers.tiff import ASCII, BYTE, RATIONAL, SHORT, UNDEFINED, build_tiff, exif_payload

    tiff = build_tiff(
        [(0x010F, ASCII, "ExampleCam"), (0x0110, ASCII, "Model X"),
         (0x0112, SHORT, orientation),
         (0x0131, ASCII, "Firmware 1.0"), (0x0132, ASCII, "2024:05:01 14:30:00")],
        exif=[(0x829A, RATIONAL, (1, 250)), (0x829D, RATIONAL, (18, 10)),
              (0x8827, SHORT, 100), (0x9003, ASCII, "2024:05:01 14:30:00"),
              (0x9011, ASCII, "+02:00"), (0x9209, SHORT, 0x10),
              (0x920A, RATIONAL, (42, 10)), (0xA431, ASCII, "SN-0001")],
        gps=[(0x0000, BYTE, b"\x02\x03\x00\x00"), (0x0001, ASCII, "N"),
             (0x0002, RATIONAL, [(48, 1), (51, 1), (3012, 100)]), (0x0003, ASCII, "E"),
             (0x0004, RATIONAL, [(2, 1), (17, 1), (4013, 100)]), (0x0005, BYTE, 0),
             (0x0006, RATIONAL, (35, 1)), (0x0007, RATIONAL, [(12, 1), (30, 1), (15, 1)]),
             (0x001D, ASCII, "2024:05:01")],
        ifd1=[(0x0103, SHORT, 6)],
        thumbnail=make_jpeg(width=160, height=120),
        order=order,
    )
    return jpeg_file(app(1, exif_payload(tiff)), xmp_segment(SAMPLE_XMP),
                     width=PHOTO_WIDTH, height=PHOTO_HEIGHT, trailing=trailing)


def sample_photo_rotated(order: str = "<") -> bytes:
    """sample_photo() taken with the camera turned: Orientation = 6 (the
    stored 48x32 image is shown rotated 90 degrees clockwise, as 32x48)."""
    return sample_photo(order=order, orientation=6)


def scan_range(data: bytes) -> tuple[int, int]:
    """(start, end) of the entropy-coded data of the main image's scan (the
    files built here have one scan and no restart markers). The segments
    are walked from SOI, so the SOS of an EXIF thumbnail (inside APP1) is
    never taken for the main image's."""
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            break
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        if length < 2:
            break
        if data[pos + 1] == 0xDA:
            start = pos + 2 + length
            return start, data.index(EOI, start)
        pos += 2 + length
    raise ValueError("no SOS segment found")


def corrupt_scan(data: bytes, where: float = 0.5) -> bytes:
    """``data`` with one bit of the scan data flipped, ``where`` of the way
    through it; never a 0xFF byte or a stuffed 0x00, so the marker structure
    stays valid and only the picture changes."""
    start, end = scan_range(data)
    i = start + int((end - start - 1) * where)
    while i < end and (data[i] == 0xFF or data[i - 1] == 0xFF or data[i] ^ 0x10 == 0xFF):
        i += 1
    if i >= end:
        raise ValueError("no byte in the scan can be changed safely")
    out = bytearray(data)
    out[i] ^= 0x10
    return bytes(out)


def kitchen_sink_jpeg(order: str = "<") -> bytes:
    """Every kind of metadata metaex reads: EXIF (GPS, thumbnail), XMP and
    Extended XMP, IPTC, ICC, comment, JFIF, Adobe, an unknown APPn, MPF
    extra images and trailing data."""
    photo = sample_photo(order=order)
    extras = (
        [app(0, b"JFIF\x00\x01\x02\x01\x00\x48\x00\x48\x00\x00")]
        + extended_xmp_segments(SAMPLE_XMP, chunk_size=400)
        + [photoshop_segment(photoshop_resource(0x0404, iim((1, 90, b"\x1b%G"), (2, 80, "Jane"),
                                                             (2, 90, "Paris"), (2, 25, "a"))),
                             photoshop_resource(0x040C, bytes(40)))]
        + icc_segments(icc_profile(), 300)
        + [segment(0xFE, b"a comment"), app(14, b"Adobe\x00\x64\x00\x00\x00\x00\x01"),
           app(12, b"Ducky\x00\x01")]
    )
    # Insert the extra segments right after SOI, then the MPF segment before
    # them (its offsets computed with everything in place), the MPF extra
    # image after EOI and trailing data.
    body = photo[:2] + b"".join(extras) + photo[2:]
    extra_image = make_jpeg(width=64, height=48)
    return with_mpf(body, [(0x010001, extra_image)], order) + b"trailing secret"


def make_jpeg(*segments: bytes, width: int = 8, height: int = 8,
              scan: bytes = SCAN_DATA, eoi: bool = True, trailing: bytes = b"") -> bytes:
    """SOI, the given segments, a minimal frame and scan, EOI, trailing bytes."""
    return (SOI + b"".join(segments) + dqt() + sof0(width, height) + dht() + sos()
            + scan + (EOI if eoi else b"") + trailing)
