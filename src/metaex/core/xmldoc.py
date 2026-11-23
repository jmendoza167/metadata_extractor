"""Safe parsing of the XML parts inside Office documents, keeping byte offsets.

Office parts are untrusted XML. They are parsed with expat, without
namespace processing (every prefix stays exactly as written; namespaces are
resolved here), and refused (MalformedFileError) if they:

- contain a DOCTYPE or an ENTITY declaration, or use an entity other than
  the five predefined ones (no entity expansion, no external entities),
- are not UTF-8,
- have more than MAX_ELEMENTS elements or nest deeper than MAX_DEPTH.

Every element records where its start tag begins and ends and where the
element ends, in bytes of the original data. That lets an edit change just
the bytes it needs (see XmlEditor), leaving every namespace declaration,
prefix, quote and space of the rest of the part exactly as it was.
"""

from __future__ import annotations

import re
import xml.parsers.expat
from collections.abc import Callable, Iterator

from metaex.core.errors import MalformedFileError

MAX_ELEMENTS = 1_000_000
MAX_DEPTH = 256
XML_NS = "http://www.w3.org/XML/1998/namespace"

_DECLARED_ENCODING = re.compile(rb"""^(?:\xef\xbb\xbf)?<\?xml[^>]*?encoding\s*=\s*["']([^"']+)["']""")
_UTF8_NAMES = {b"utf-8", b"utf8", b"us-ascii", b"ascii"}


class XmlText:
    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value


class XmlComment:
    __slots__ = ("value", "start", "end")

    def __init__(self, value: str, start: int, end: int) -> None:
        self.value, self.start, self.end = value, start, end


class XmlPI:
    __slots__ = ("target", "data", "start", "end")

    def __init__(self, target: str, data: str, start: int, end: int) -> None:
        self.target, self.data, self.start, self.end = target, data, start, end


class XmlElement:
    __slots__ = ("name", "attrs", "ns", "parent", "children", "start", "start_end", "end",
                 "empty", "line")

    def __init__(self, name: str, attrs: list[tuple[str, str]], ns: dict[str, str],
                 parent: XmlElement | None, start: int, line: int) -> None:
        self.name = name
        """Qualified name as written, e.g. "w:p"."""
        self.attrs = attrs
        """Attributes as written, in order (values unescaped), with xmlns declarations."""
        self.ns = ns
        """Namespaces in scope: prefix -> URI ("" is the default namespace)."""
        self.parent = parent
        self.children: list[XmlElement | XmlText | XmlComment | XmlPI] = []
        self.start = start
        """Offset of the "<" of the start tag."""
        self.start_end = start
        """Offset just after the start tag's ">"."""
        self.end = start
        """Offset just after the element (after "/>" or the end tag's ">")."""
        self.empty = False
        """True for a self-closing tag (<a/>)."""
        self.line = line

    def __repr__(self) -> str:
        return f"<XmlElement {self.name} at {self.start}>"

    @property
    def prefix(self) -> str:
        return self.name.split(":", 1)[0] if ":" in self.name else ""

    @property
    def local(self) -> str:
        return self.name.split(":", 1)[-1]

    @property
    def uri(self) -> str | None:
        return self.ns.get(self.prefix)

    def attr_uri(self, name: str) -> str | None:
        """Namespace of an attribute (unprefixed attributes have none)."""
        return self.ns.get(name.split(":", 1)[0]) if ":" in name else None

    def is_(self, uri: str | None, local: str) -> bool:
        return self.local == local and self.uri == uri

    def get(self, local: str, uri: str | None = None) -> str | None:
        """Value of the attribute with this local name and namespace."""
        for name, value in self.attrs:
            if name.split(":", 1)[-1] == local and self.attr_uri(name) == uri \
                    and not is_namespace_declaration(name):
                return value
        return None

    def attr_name(self, local: str, uri: str | None = None) -> str | None:
        """The attribute's qualified name as written, if present."""
        for name, _value in self.attrs:
            if name.split(":", 1)[-1] == local and self.attr_uri(name) == uri \
                    and not is_namespace_declaration(name):
                return name
        return None

    def elements(self) -> Iterator[XmlElement]:
        return (c for c in self.children if isinstance(c, XmlElement))

    def child(self, uri: str | None, local: str) -> XmlElement | None:
        return next((c for c in self.elements() if c.is_(uri, local)), None)

    def iter(self, skip: Callable[[XmlElement], bool] | None = None) -> Iterator[XmlElement]:
        """This element and every element inside it, in document order (no
        recursion); elements for which ``skip`` is true are left out with
        their contents."""
        stack = [self]
        while stack:
            element = stack.pop()
            if skip is not None and skip(element):
                continue
            yield element
            stack.extend(reversed([c for c in element.children if isinstance(c, XmlElement)]))

    def find_all(self, uri: str | None, local: str) -> list[XmlElement]:
        return [e for e in self.iter() if e.is_(uri, local)]

    def text(self) -> str:
        """All text inside the element, in order."""
        parts = []
        for e in self.iter():
            parts += [c.value for c in e.children if isinstance(c, XmlText)]
        return "".join(parts)

    def own_text(self) -> str:
        """Text directly inside the element (not in child elements)."""
        return "".join(c.value for c in self.children if isinstance(c, XmlText))


class XmlDocument:
    __slots__ = ("data", "root", "prolog", "epilog", "element_count", "what")

    def __init__(self, data: bytes, root: XmlElement, prolog: list, epilog: list,
                 element_count: int, what: str) -> None:
        self.data = data
        self.root = root
        self.prolog = prolog
        """Comments and processing instructions before the root (not the XML declaration)."""
        self.epilog = epilog
        self.element_count = element_count
        self.what = what
        """Where the XML comes from, for messages (e.g. "word/document.xml")."""

    def iter(self) -> Iterator[XmlElement]:
        return self.root.iter()

    def find_all(self, uri: str | None, local: str) -> list[XmlElement]:
        return self.root.find_all(uri, local)

    def comments(self) -> list[XmlComment]:
        out = [n for n in self.prolog + self.epilog if isinstance(n, XmlComment)]
        for e in self.iter():
            out += [c for c in e.children if isinstance(c, XmlComment)]
        return out

    def processing_instructions(self) -> list[XmlPI]:
        out = [n for n in self.prolog + self.epilog if isinstance(n, XmlPI)]
        for e in self.iter():
            out += [c for c in e.children if isinstance(c, XmlPI)]
        return out


def is_namespace_declaration(name: str) -> bool:
    return name == "xmlns" or name.startswith("xmlns:")


def check_no_dtd(data: bytes, what: str) -> None:
    """Refuse XML that declares a DOCTYPE or entities, without parsing it
    (for parts that are copied unchanged and never parsed)."""
    if b"<!DOCTYPE" in data:
        raise MalformedFileError(f"{what} contains a DOCTYPE (not allowed in Office documents; "
                                 "possible XML entity attack); refused")
    if b"<!ENTITY" in data:
        raise MalformedFileError(f"{what} contains an ENTITY declaration (possible XML entity "
                                 "attack); refused")


def check_utf8(data: bytes, what: str) -> None:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in data[:256]:
        raise MalformedFileError(f"{what} is not UTF-8 (UTF-16 XML is not supported); refused")
    declared = _DECLARED_ENCODING.match(data)
    if declared and declared.group(1).strip().lower() not in _UTF8_NAMES:
        raise MalformedFileError(f"{what} declares the "
                                 f"{declared.group(1).decode('ascii', 'replace')!r} encoding; "
                                 "only UTF-8 is supported; refused")


def _tag_end(data: bytes, pos: int, what: str) -> int:
    """Offset just after the ">" closing the tag that starts at ``pos``.

    Quotes are skipped, so a ">" inside an attribute value doesn't count.
    Linear in the tag's length; the data is already known to be
    well-formed."""
    n = len(data)
    i = pos
    while i < n:
        c = data[i]
        if c in (0x22, 0x27):  # " or '
            close = data.find(bytes([c]), i + 1)
            if close < 0:
                break
            i = close + 1
            continue
        if c == 0x3E:  # >
            return i + 1
        i += 1
    raise MalformedFileError(f"{what}: unterminated tag at offset {pos}")


class _Builder:
    def __init__(self, parser: xml.parsers.expat.XMLParserType, data: bytes, what: str) -> None:
        self.parser = parser
        self.data = data
        self.what = what
        self.stack: list[XmlElement] = []
        self.root: XmlElement | None = None
        self.prolog: list = []
        self.epilog: list = []
        self.count = 0

    def start(self, name: str, attr_list: list[str]) -> None:
        self.count += 1
        if self.count > MAX_ELEMENTS:
            raise MalformedFileError(f"{self.what} has more than {MAX_ELEMENTS} elements")
        if len(self.stack) >= MAX_DEPTH:
            raise MalformedFileError(f"{self.what}: elements are nested more than {MAX_DEPTH} "
                                     "deep")
        attrs = list(zip(attr_list[0::2], attr_list[1::2]))
        parent = self.stack[-1] if self.stack else None
        ns = parent.ns if parent is not None else {"xml": XML_NS}
        if any(is_namespace_declaration(k) for k, _v in attrs):
            ns = dict(ns)  # copied only where declarations change it
            for key, value in attrs:
                if key == "xmlns":
                    ns[""] = value
                elif key.startswith("xmlns:"):
                    ns[key[6:]] = value
        pos = self.parser.CurrentByteIndex
        element = XmlElement(name, attrs, ns, parent, pos, self.parser.CurrentLineNumber)
        for qname in [name] + [k for k, _v in attrs if not is_namespace_declaration(k)]:
            if ":" in qname and qname.split(":", 1)[0] not in ns:
                raise MalformedFileError(f"{self.what}: unbound namespace prefix in {qname!r} "
                                         f"at line {element.line}")
        element.start_end = _tag_end(self.data, pos, self.what)
        element.empty = self.data[element.start_end - 2:element.start_end] == b"/>"
        if parent is None:
            self.root = element
        else:
            parent.children.append(element)
        self.stack.append(element)

    def end(self, _name: str) -> None:
        element = self.stack.pop()
        pos = self.parser.CurrentByteIndex
        element.end = element.start_end if element.empty else _tag_end(self.data, pos, self.what)

    def chars(self, data: str) -> None:
        if not self.stack:
            return
        children = self.stack[-1].children
        if children and isinstance(children[-1], XmlText):
            children[-1].value += data
        else:
            children.append(XmlText(data))

    def _misc(self, node: XmlComment | XmlPI) -> None:
        if self.stack:
            self.stack[-1].children.append(node)
        elif self.root is None:
            self.prolog.append(node)
        else:
            self.epilog.append(node)

    def comment(self, value: str) -> None:
        pos = self.parser.CurrentByteIndex
        end = self.data.find(b"-->", pos + 4)
        self._misc(XmlComment(value, pos, end + 3 if end >= 0 else pos))

    def pi(self, target: str, value: str) -> None:
        pos = self.parser.CurrentByteIndex
        end = self.data.find(b"?>", pos + 2)
        self._misc(XmlPI(target, value, pos, end + 2 if end >= 0 else pos))

    def refuse_doctype(self, *_args) -> None:
        raise MalformedFileError(f"{self.what} contains a DOCTYPE (not allowed in Office "
                                 "documents; possible XML entity attack); refused")

    def refuse_entity_declaration(self, *_args) -> None:
        raise MalformedFileError(f"{self.what} contains an ENTITY declaration (possible XML "
                                 "entity attack); refused")

    def refuse_entity_reference(self, name: str, *_args) -> None:
        raise MalformedFileError(f"{self.what} uses the undefined entity &{name};; refused")

    def refuse_external_entity(self, *_args) -> int:
        raise MalformedFileError(f"{self.what} refers to an external entity; refused")


def parse_xml(data: bytes | bytearray | memoryview, what: str = "XML part") -> XmlDocument:
    """Parse one XML part; raises MalformedFileError (see the module docstring)."""
    data = bytes(data)
    check_utf8(data, what)
    check_no_dtd(data, what)
    parser = xml.parsers.expat.ParserCreate()
    parser.ordered_attributes = True
    parser.buffer_text = True
    parser.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)
    b = _Builder(parser, data, what)
    parser.StartElementHandler = b.start
    parser.EndElementHandler = b.end
    parser.CharacterDataHandler = b.chars
    parser.CommentHandler = b.comment
    parser.ProcessingInstructionHandler = b.pi
    parser.StartDoctypeDeclHandler = b.refuse_doctype
    parser.EntityDeclHandler = b.refuse_entity_declaration
    parser.SkippedEntityHandler = b.refuse_entity_reference
    parser.ExternalEntityRefHandler = b.refuse_external_entity
    try:
        parser.Parse(data, True)
    except xml.parsers.expat.ExpatError as exc:
        raise MalformedFileError(f"{what} is not well-formed XML: {exc}") from None
    except RecursionError:
        raise MalformedFileError(f"{what} is nested too deeply") from None
    if b.root is None:
        raise MalformedFileError(f"{what} has no root element")
    return XmlDocument(data, b.root, b.prolog, b.epilog, b.count, what)
