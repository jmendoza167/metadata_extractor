"""Build small, structurally valid JPEG files for tests.

The images are not meant to decode to real pictures; they only need the
marker structure metaex reads.
"""

from __future__ import annotations

import struct

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"

# Entropy-coded data containing a stuffed 0xFF byte (FF00).
SCAN_DATA = b"\x12\x34\xff\x00\x56\x78"


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


def make_mpf_jpeg(extra_images, order: str = "<") -> bytes:
    """A JPEG whose APP2 MPF index lists the given (type, image_bytes) extras,
    which are appended after the primary image's EOI."""
    from helpers.tiff import LONG, UNDEFINED, ifd_bytes, tiff_header

    def build(offsets):
        bo = "<" if order == "<" else ">"
        entries = struct.pack(bo + "IIIHH", 0x20030000, 0, 0, 0, 0)
        for (kind, image), offset in zip(extra_images, offsets):
            entries += struct.pack(bo + "IIIHH", kind, len(image), offset, 0, 0)
        ifd = [(0xB000, UNDEFINED, b"0100"), (0xB001, LONG, 1 + len(extra_images)),
               (0xB002, UNDEFINED, entries)]
        tiff = tiff_header(order) + ifd_bytes(ifd, 8, order)
        return make_jpeg(app(2, b"MPF\x00" + tiff))

    primary = build([0] * len(extra_images))
    header_offset = 2 + 4 + 4  # SOI, APP2 marker + length, "MPF\0"
    offsets, position = [], len(primary)
    for _, image in extra_images:
        offsets.append(position - header_offset)
        position += len(image)
    primary = build(offsets)
    return primary + b"".join(image for _, image in extra_images)


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


def make_jpeg(*segments: bytes, width: int = 8, height: int = 8,
              scan: bytes = SCAN_DATA, eoi: bool = True, trailing: bytes = b"") -> bytes:
    """SOI, the given segments, a minimal frame and scan, EOI, trailing bytes."""
    return (SOI + b"".join(segments) + dqt() + sof0(width, height) + dht() + sos()
            + scan + (EOI if eoi else b"") + trailing)
