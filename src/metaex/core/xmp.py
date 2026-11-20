"""XMP (Adobe Extensible Metadata Platform) packets: RDF/XML properties.

Used by JPEG (APP1), and later PNG (iTXt), WebP (XMP chunk) and TIFF.

Security: XMP is untrusted XML. Any packet containing a DOCTYPE or ENTITY
declaration is refused before parsing, which rules out entity-expansion
("billion laughs") and external-entity attacks. Packets that are not UTF-8
(or plain ASCII) are refused too, because the check is done on raw bytes and
a UTF-16 packet could hide a declaration from it.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from metaex.core.errors import MalformedFileError

MAX_XMP_SIZE = 16 * 1024 * 1024
MAX_PROPERTIES = 10_000
MAX_DEPTH = 64

RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XML_NS = "http://www.w3.org/XML/1998/namespace"
_RDF = "{" + RDF_NS + "}"
_XML = "{" + XML_NS + "}"
_CONTAINERS = {_RDF + "Seq", _RDF + "Bag", _RDF + "Alt"}

_UNSAFE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)

# Fallback prefixes when a packet doesn't declare one we can recover.
KNOWN_PREFIXES = {
    "http://ns.adobe.com/xap/1.0/": "xmp",
    "http://ns.adobe.com/xap/1.0/mm/": "xmpMM",
    "http://ns.adobe.com/xap/1.0/rights/": "xmpRights",
    "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#": "stEvt",
    "http://ns.adobe.com/xap/1.0/sType/ResourceRef#": "stRef",
    "http://ns.adobe.com/xmp/note/": "xmpNote",
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://ns.adobe.com/exif/1.0/": "exif",
    "http://cipa.jp/exif/1.0/": "exifEX",
    "http://ns.adobe.com/tiff/1.0/": "tiff",
    "http://ns.adobe.com/photoshop/1.0/": "photoshop",
    "http://ns.adobe.com/camera-raw-settings/1.0/": "crs",
    "http://ns.adobe.com/exif/1.0/aux/": "aux",
    "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/": "Iptc4xmpCore",
    "http://iptc.org/std/Iptc4xmpExt/2008-02-29/": "Iptc4xmpExt",
    "http://ns.google.com/photos/1.0/camera/": "GCamera",
    "http://ns.google.com/photos/1.0/container/": "GContainer",
    "http://ns.google.com/photos/1.0/image/": "GImage",
    "http://ns.google.com/photos/1.0/depthmap/": "GDepth",
    "http://www.metadataworkinggroup.com/schemas/regions/": "mwg-rs",
    "http://ns.apple.com/ImageIO/1.0/": "iio",
}

SENSITIVE_NAMES = {
    "DocumentID", "OriginalDocumentID", "InstanceID", "History", "DerivedFrom",
    "Ingredients", "Pantry", "PreservedFileName", "creator", "rights", "contributor",
    "publisher", "Owner", "OwnerName", "CameraOwnerName", "SerialNumber",
    "BodySerialNumber", "LensSerialNumber", "ImageUniqueID", "CreatorTool", "Make",
    "Model", "Artist", "Copyright", "UserComment", "CreatorContactInfo", "City", "State",
    "Country", "CountryCode", "Location", "Sublocation", "LocationCreated",
    "LocationShown", "AuthorsPosition", "CaptionWriter", "Credit", "Source",
    "WebStatement", "softwareAgent", "when", "PersonInImage", "Regions", "Name",
    "description", "title", "subject", "Lens", "LensModel", "LensID", "Software",
    "HostComputer",
}


@dataclass
class XmpProperty:
    path: str
    """prefix:name, with /prefix:field for struct fields and [n] for array items."""
    value: str
    sensitive: bool


@dataclass
class XmpResult:
    properties: list[XmpProperty] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check_xmp_safety(packet: bytes) -> None:
    """Raise MalformedFileError if the packet must not be parsed."""
    if len(packet) > MAX_XMP_SIZE:
        raise MalformedFileError(f"XMP packet is larger than {MAX_XMP_SIZE} bytes")
    if packet.startswith((b"\xfe\xff", b"\xff\xfe")) or b"\x00" in packet[:1024]:
        raise MalformedFileError("XMP packet is not UTF-8; refused")
    if _UNSAFE.search(packet):
        raise MalformedFileError(
            "XMP contains a DOCTYPE or ENTITY declaration (possible XML bomb); refused")


def parse_xmp(packet: bytes) -> XmpResult:
    """Every simple property of an XMP packet as prefix:name = value.

    Raises MalformedFileError if the packet is unsafe or not well-formed.
    """
    packet = bytes(packet)
    check_xmp_safety(packet)
    prefixes: dict[str, str] = {}
    try:
        events = ET.iterparse(io.BytesIO(packet), events=("start-ns",))
        for _event, (prefix, uri) in events:
            if prefix:
                prefixes.setdefault(uri, prefix)
        root = events.root
    except (ET.ParseError, ValueError) as exc:
        raise MalformedFileError(f"XMP is not well-formed XML: {exc}") from None

    walker = _Walker(prefixes)
    rdf = root if root.tag == _RDF + "RDF" else root.find(".//" + _RDF + "RDF")
    if rdf is None:
        walker.result.warnings.append("XMP packet has no rdf:RDF element; no properties read")
        return walker.result
    for description in rdf.findall(_RDF + "Description"):
        walker.description(description, "", 0)
    if walker.truncated:
        walker.result.warnings.append(
            f"XMP has more than {MAX_PROPERTIES} properties; the rest are not shown")
    gps = [p.path for p in walker.result.properties if _is_gps(p.path)]
    if gps:
        walker.result.warnings.append(
            "CRITICAL: XMP contains GPS data (" + ", ".join(gps[:4])
            + (", ..." if len(gps) > 4 else "") + ")")
    return walker.result


def _segments(path: str) -> list[str]:
    """Local names along a property path, without prefixes or [n]."""
    out = []
    for part in path.split("/"):
        part = part.split("[", 1)[0]
        out.append(part.split(":", 1)[-1])
    return out


def _is_gps(path: str) -> bool:
    return any(name.startswith("GPS") for name in _segments(path))


def is_sensitive_path(path: str) -> bool:
    return any(
        name in SENSITIVE_NAMES or name.startswith("GPS") or "Date" in name
        for name in _segments(path)
    )


class _Walker:
    def __init__(self, prefixes: dict[str, str]) -> None:
        self.prefixes = prefixes
        self.result = XmpResult()
        self.truncated = False

    def qname(self, tag: str) -> str:
        if tag.startswith("{"):
            uri, local = tag[1:].split("}", 1)
            prefix = self.prefixes.get(uri) or KNOWN_PREFIXES.get(uri) or uri
            return f"{prefix}:{local}"
        return tag

    def emit(self, path: str, value: str) -> None:
        if len(self.result.properties) >= MAX_PROPERTIES:
            self.truncated = True
            return
        self.result.properties.append(XmpProperty(path, value, is_sensitive_path(path)))

    @staticmethod
    def _plain_attrs(elem: ET.Element) -> list[tuple[str, str]]:
        return [(k, v) for k, v in elem.attrib.items()
                if not k.startswith(_RDF) and not k.startswith(_XML)]

    def description(self, desc: ET.Element, prefix: str, depth: int) -> None:
        """Properties of an rdf:Description: attributes and child elements."""
        for k, v in self._plain_attrs(desc):
            self.emit(prefix + self.qname(k), v)
        for child in desc:
            self.prop(child, prefix + self.qname(child.tag), depth + 1)

    def prop(self, elem: ET.Element, path: str, depth: int) -> None:
        if depth > MAX_DEPTH:
            self.emit(path, "(nested too deeply; not shown)")
            return
        attrs = self._plain_attrs(elem)
        resource = elem.get(_RDF + "resource")
        children = list(elem)
        if resource is not None:
            self.emit(path, resource)
        for k, v in attrs:
            self.emit(f"{path}/{self.qname(k)}", v)
        if not children:
            text = (elem.text or "").strip()
            if text or (not attrs and resource is None):
                self.emit(path, text)
            return
        if len(children) == 1 and children[0].tag in _CONTAINERS:
            items = [li for li in children[0] if li.tag == _RDF + "li"]
            if all(self._is_simple(li) for li in items):
                self.emit(path, "; ".join((li.text or "").strip() for li in items))
            else:
                for i, li in enumerate(items, 1):
                    self.prop(li, f"{path}[{i}]", depth + 1)
            return
        for child in children:
            if child.tag == _RDF + "Description":
                self.description(child, path + "/", depth + 1)
            else:
                self.prop(child, f"{path}/{self.qname(child.tag)}", depth + 1)

    def _is_simple(self, li: ET.Element) -> bool:
        return len(li) == 0 and not self._plain_attrs(li)
