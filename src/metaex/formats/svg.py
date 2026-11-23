"""SVG: read the metadata, editor data and file paths in a drawing.

An SVG file is XML text. Besides the drawing it often carries:
- a <metadata> element with RDF / Dublin Core (author, date, rights, ...),
- <title> and <desc> elements (accessibility text, often the file's name),
- XML comments ("Generator: ..."), processing instructions,
- editor data: Inkscape/Sodipodi, Illustrator, Sketch, Affinity, ...
  namespaces with document names, export paths and program versions,
- absolute local file paths (C:\\Users\\<name>\\..., /home/<name>/...),
- embedded images as data: URIs, which carry their own metadata.

Security: SVG is untrusted XML. Files with an ENTITY declaration, or with
any DOCTYPE other than the standard W3C SVG 1.0 / 1.1 one (no internal
subset), are refused before parsing (no entity-expansion or
external-entity attacks); the DTD a standard DOCTYPE names is never
loaded, and entity references other than the predefined ones are refused.
Only UTF-8 is accepted (so those checks can't be bypassed), and sizes,
element counts and nesting depth are capped. The file is parsed
with expat without namespace processing, so every prefix is kept exactly
as written; namespaces are resolved here.
"""

from __future__ import annotations

import base64
import binascii
import re
import urllib.parse
import xml.parsers.expat
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from metaex.core import filetype, xmp
from metaex.core.errors import MalformedFileError
from metaex.core.result import Section

MAX_SVG_SIZE = 64 * 1024 * 1024
MAX_ELEMENTS = 500_000
MAX_DEPTH = 256
MAX_EMBEDDED = 1000
MAX_EMBEDDED_TOTAL = 64 * 1024 * 1024
"""Decoded data: URIs are examined while their sizes add up to at most this."""
MAX_NESTED_SVG = 3
MAX_LISTED = 200
"""At most this many fields per section; the rest are counted."""
MAX_REFERENCE_WARNINGS = 20

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

# Namespaces of drawing programs: their elements and attributes only help
# the program re-open the file (layers, guides, window size, export
# settings) and are not needed to display the drawing.
EDITOR_NAMESPACES = {
    "http://www.inkscape.org/namespaces/inkscape": "Inkscape",
    "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd": "Sodipodi (Inkscape)",
    "http://inkscape.sourceforge.net/DTD/sodipodi-0.dtd": "Sodipodi (Inkscape)",
    "http://www.openswatchbook.org/uri/2009/osb": "Open Swatch Book (Inkscape)",
    "http://www.bohemiancoding.com/sketch/ns": "Sketch",
    "http://www.serif.com/": "Serif (Affinity)",
    "http://schemas.microsoft.com/visio/2003/SVGExtensions/": "Microsoft Visio",
    "http://krita.org/namespaces/svg/krita": "Krita",
}
EDITOR_NAMESPACE_PREFIXES = {"http://ns.adobe.com/": "Adobe Illustrator"}

_XML_SPACE = "[ \t\r\n]"
_STANDARD_DOCTYPE = re.compile(
    f"<!DOCTYPE{_XML_SPACE}+svg{_XML_SPACE}+PUBLIC{_XML_SPACE}+"
    r"""(?P<q1>["'])(?P<public>[^"'<>\[\]]*)(?P=q1)""" f"{_XML_SPACE}+"
    r"""(?P<q2>["'])(?P<system>[^"'<>\[\]]*)(?P=q2)""" f"{_XML_SPACE}*>")
STANDARD_DOCTYPES = {
    ("-//W3C//DTD SVG 1.0//EN", "http://www.w3.org/TR/2001/REC-SVG-20010904/DTD/svg10.dtd"):
        "SVG 1.0",
    ("-//W3C//DTD SVG 1.1//EN", "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd"): "SVG 1.1",
}
"""The only DOCTYPEs accepted: (public ID, system ID) -> SVG version. The DTD
is never loaded; the DOCTYPE has no effect on the drawing."""
_PROLOG_MARKUP = re.compile(r"<\?|<!--")
_MARKUP_OR_REFERENCE = re.compile(r"<!--|<!\[CDATA\[|<\?|&")
_REFERENCE = re.compile(r"&(#[0-9]+|#x[0-9A-Fa-f]+|[A-Za-z_:][A-Za-z0-9_.:\-]*);")
_PREDEFINED_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
_DECLARED_ENCODING = re.compile(r"""<\?xml[^>]*?encoding\s*=\s*["']([^"']+)["']""")
_UTF8_NAMES = {"utf-8", "utf8", "us-ascii", "ascii"}


def editor_tool(uri: str | None) -> str | None:
    """The program a namespace belongs to, or None if it's not an editor namespace."""
    if uri is None:
        return None
    if uri in EDITOR_NAMESPACES:
        return EDITOR_NAMESPACES[uri]
    for prefix, tool in EDITOR_NAMESPACE_PREFIXES.items():
        if uri.startswith(prefix):
            return tool
    return None


# -- document model ----------------------------------------------------------------


@dataclass
class Text:
    value: str
    cdata: bool = False


@dataclass
class Comment:
    value: str
    line: int


@dataclass
class PI:
    target: str
    data: str
    line: int


@dataclass(eq=False)
class Element:
    name: str
    """Qualified name as written, e.g. "svg" or "sodipodi:namedview"."""
    attrs: list[tuple[str, str]]
    """Attributes as written, in order (including xmlns declarations)."""
    line: int
    ns: dict[str, str] = field(default_factory=dict, repr=False)
    """Namespaces in scope: prefix -> URI ("" is the default namespace)."""
    children: list = field(default_factory=list, repr=False)
    parent: Element | None = field(default=None, repr=False)

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

    def get(self, local: str, uri: str | None = None) -> str | None:
        for name, value in self.attrs:
            if name.split(":", 1)[-1] == local and self.attr_uri(name) == uri:
                return value
        return None

    def is_svg(self, local: str) -> bool:
        return self.local == local and self.uri == SVG_NS


Node = Text | Comment | PI | Element


@dataclass
class Document:
    root: Element
    declaration: tuple[str, str | None, int] | None = None
    """(version, encoding, standalone) of the XML declaration, if any."""
    prolog: list = field(default_factory=list)
    """Comments and processing instructions before the root element."""
    epilog: list = field(default_factory=list)
    element_count: int = 0
    doctype: tuple[str, str, str, int] | None = None
    """(SVG version, public ID, system ID, line) of the standard DOCTYPE, if any."""


def is_namespace_declaration(name: str) -> bool:
    return name == "xmlns" or name.startswith("xmlns:")


_DOCTYPE_START = re.compile(r"<!\s*DOCTYPE", re.IGNORECASE)
_ENTITY_START = re.compile(r"<!\s*ENTITY", re.IGNORECASE)
_SPACES = re.compile(r"[ \t\r\n]*")
_CLOSERS = {"<!--": "-->", "<![CDATA[": "]]>", "<?": "?>"}


def _only_prolog(text: str, end: int) -> bool:
    """True if text[:end] holds only what may come before a DOCTYPE: the XML
    declaration, processing instructions, comments and whitespace."""
    pos = _SPACES.match(text, 0, end).end()
    while pos < end:
        m = _PROLOG_MARKUP.match(text, pos)
        if m is None:
            return False
        close = text.find(_CLOSERS[m.group()], m.end(), end)
        if close < 0:
            return False
        pos = _SPACES.match(text, close + len(_CLOSERS[m.group()]), end).end()
    return True


def _undeclared_entity(text: str) -> str | None:
    """The first entity reference other than the five predefined ones and
    character references (outside comments, CDATA sections and processing
    instructions), or None. With a DOCTYPE whose DTD is not loaded, expat
    silently drops such references (in attribute values without telling at
    all), which would change the drawing, so they are refused."""
    pos = 0
    while True:
        m = _MARKUP_OR_REFERENCE.search(text, pos)
        if m is None:
            return None
        if m.group() == "&":
            ref = _REFERENCE.match(text, m.start())
            if ref is None:
                pos = m.end()  # not a reference: expat rejects the file
                continue
            name = ref.group(1)
            if not name.startswith("#") and name not in _PREDEFINED_ENTITIES:
                return name
            pos = ref.end()
            continue
        close = text.find(_CLOSERS[m.group()], m.end())
        if close < 0:
            return None  # unterminated: expat rejects the file
        pos = close + len(_CLOSERS[m.group()])


def check_doctype(text: str) -> str | None:
    """The SVG version of the file's DOCTYPE ("SVG 1.0" or "SVG 1.1"), or
    None if it has none; raises MalformedFileError for every other DOCTYPE.

    Only the official W3C SVG 1.0 and 1.1 DOCTYPEs are accepted: exactly
    that public and system ID (quotes and whitespace between the parts may
    vary), no internal subset, a single DOCTYPE, before the root element.
    ENTITY declarations are always refused, and so is any entity reference
    other than &amp; &lt; &gt; &quot; &apos; and character references."""
    if _ENTITY_START.search(text):
        raise MalformedFileError("SVG contains an ENTITY declaration (possible XML entity "
                                 "attack); refused")
    first = _DOCTYPE_START.search(text)
    if first is None:
        return None
    if _DOCTYPE_START.search(text, first.end()):
        raise MalformedFileError("SVG has more than one DOCTYPE; refused")
    if not _only_prolog(text, first.start()):
        raise MalformedFileError("SVG has a DOCTYPE after the start of the drawing (it may "
                                 "only come before the <svg> element); refused")
    standard = _STANDARD_DOCTYPE.match(text, first.start())
    version = None
    if standard is not None:
        version = STANDARD_DOCTYPES.get((standard.group("public"), standard.group("system")))
    if version is None:
        close = text.find(">", first.start())
        declaration = text[first.start():close + 1 if close >= 0 else len(text)]
        if "[" in declaration:
            raise MalformedFileError("SVG has a DOCTYPE with an internal subset (it can define "
                                     "entities and change the drawing); refused")
        shown = " ".join(declaration.split())
        shown = shown if len(shown) <= 200 else shown[:200] + "..."
        raise MalformedFileError(f"SVG has a DOCTYPE that is not the standard SVG 1.0 or 1.1 "
                                 f"one ({shown!r}); refused")
    name = _undeclared_entity(text)
    if name is not None:
        raise MalformedFileError(f"SVG uses the entity &{name}; which is not defined in the "
                                 "file (metaex never loads the DTD); refused")
    return version


def decode(data: bytes) -> str:
    """The file as text; raises MalformedFileError for anything metaex won't
    parse: too large, not UTF-8, with an ENTITY declaration, or with a
    DOCTYPE other than the standard SVG 1.0 / 1.1 one (see check_doctype)."""
    if len(data) > MAX_SVG_SIZE:
        raise MalformedFileError(f"SVG is larger than {MAX_SVG_SIZE // (1024 * 1024)} MiB")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in data[:1024]:
        raise MalformedFileError("SVG is not UTF-8 (UTF-16 is not supported); refused")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MalformedFileError(f"SVG is not valid UTF-8 ({exc.reason} at byte "
                                 f"{exc.start}); refused") from None
    declared = _DECLARED_ENCODING.match(text)
    if declared and declared.group(1).strip().lower() not in _UTF8_NAMES:
        raise MalformedFileError(f"SVG declares the {declared.group(1)!r} encoding; only "
                                 "UTF-8 is supported; refused")
    check_doctype(text)
    return text


class _Builder:
    def __init__(self, parser: xml.parsers.expat.XMLParserType) -> None:
        self.parser = parser
        self.stack: list[Element] = []
        self.root: Element | None = None
        self.prolog: list = []
        self.epilog: list = []
        self.declaration: tuple[str, str | None, int] | None = None
        self.count = 0
        self.in_cdata = False
        self.doctype: tuple[str, str, str, int] | None = None

    def _line(self) -> int:
        return self.parser.CurrentLineNumber

    def start(self, name: str, attr_list: list[str]) -> None:
        self.count += 1
        if self.count > MAX_ELEMENTS:
            raise MalformedFileError(f"SVG has more than {MAX_ELEMENTS} elements")
        if len(self.stack) >= MAX_DEPTH:
            raise MalformedFileError(f"SVG elements are nested more than {MAX_DEPTH} deep")
        attrs = list(zip(attr_list[0::2], attr_list[1::2]))
        parent = self.stack[-1] if self.stack else None
        ns = dict(parent.ns) if parent is not None else {"xml": XML_NS}
        for key, value in attrs:
            if key == "xmlns":
                ns[""] = value
            elif key.startswith("xmlns:"):
                ns[key[6:]] = value
        element = Element(name, attrs, self._line(), ns, parent=parent)
        for qname in [name] + [k for k, _v in attrs if not is_namespace_declaration(k)]:
            if ":" in qname and qname.split(":", 1)[0] not in ns:
                raise MalformedFileError(f"unbound namespace prefix in {qname!r} at line "
                                         f"{element.line}")
        if parent is None:
            self.root = element
        else:
            parent.children.append(element)
        self.stack.append(element)

    def end(self, _name: str) -> None:
        self.stack.pop()

    def chars(self, data: str) -> None:
        if not self.stack:
            return  # whitespace outside the root element (expat rejects anything else)
        children = self.stack[-1].children
        if children and isinstance(children[-1], Text) and children[-1].cdata == self.in_cdata:
            children[-1].value += data
        else:
            children.append(Text(data, self.in_cdata))

    def _misc(self, node: Comment | PI) -> None:
        if self.stack:
            self.stack[-1].children.append(node)
        elif self.root is None:
            self.prolog.append(node)
        else:
            self.epilog.append(node)

    def comment(self, data: str) -> None:
        self._misc(Comment(data, self._line()))

    def pi(self, target: str, data: str) -> None:
        self._misc(PI(target, data, self._line()))

    def start_cdata(self) -> None:
        self.in_cdata = True

    def end_cdata(self) -> None:
        self.in_cdata = False

    def xml_decl(self, version: str, encoding: str | None, standalone: int) -> None:
        self.declaration = (version, encoding, standalone)

    def start_doctype(self, name: str, system_id: str | None, public_id: str | None,
                      has_internal_subset: int) -> None:
        # check_doctype() already accepted it; expat's view must agree.
        version = STANDARD_DOCTYPES.get((public_id, system_id))
        if (name != "svg" or version is None or has_internal_subset or self.doctype is not None
                or self.root is not None):
            raise MalformedFileError("SVG has a DOCTYPE that is not the standard SVG 1.0 or "
                                     "1.1 one; refused")
        self.doctype = (version, public_id, system_id, self._line())

    def refuse_entity_declaration(self, *_args) -> None:
        raise MalformedFileError("SVG contains an ENTITY declaration; refused")

    def refuse_entity_reference(self, name: str, *_args) -> None:
        raise MalformedFileError(f"SVG uses the entity &{name}; which is not defined in the "
                                 "file (metaex never loads the DTD); refused")

    def refuse_external_entity(self, *_args) -> int:
        raise MalformedFileError("SVG refers to an external entity; refused")


def parse_document(data: bytes | bytearray | memoryview) -> Document:
    """Parse SVG text into a Document; raises MalformedFileError."""
    text = decode(bytes(data))
    parser = xml.parsers.expat.ParserCreate()
    parser.ordered_attributes = True
    parser.buffer_text = True
    parser.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)
    b = _Builder(parser)
    parser.StartElementHandler = b.start
    parser.EndElementHandler = b.end
    parser.CharacterDataHandler = b.chars
    parser.CommentHandler = b.comment
    parser.ProcessingInstructionHandler = b.pi
    parser.StartCdataSectionHandler = b.start_cdata
    parser.EndCdataSectionHandler = b.end_cdata
    parser.XmlDeclHandler = b.xml_decl
    # The DTD named by a standard DOCTYPE is never loaded (no network, no
    # external entities): parameter entity parsing is off and every hook
    # that could pull in or expand an entity refuses the file.
    parser.StartDoctypeDeclHandler = b.start_doctype
    parser.EntityDeclHandler = b.refuse_entity_declaration
    parser.SkippedEntityHandler = b.refuse_entity_reference
    parser.ExternalEntityRefHandler = b.refuse_external_entity
    try:
        parser.Parse(text.encode("utf-8"), True)
    except xml.parsers.expat.ExpatError as exc:
        raise MalformedFileError(f"SVG is not well-formed XML: {exc}") from None
    except RecursionError:
        raise MalformedFileError("SVG is nested too deeply") from None
    if b.root is None:
        raise MalformedFileError("SVG has no root element")
    if b.root.local != "svg":
        raise MalformedFileError(f"the root element is <{b.root.name}>, not <svg>")
    return Document(b.root, b.declaration, b.prolog, b.epilog, b.count, b.doctype)


# -- walking and writing ----------------------------------------------------------------


def iter_elements(root: Element,
                  skip: Callable[[Element], bool] | None = None) -> Iterator[Element]:
    """Every element in document order (no recursion); elements for which
    ``skip`` is true are left out together with their contents."""
    stack = [root]
    while stack:
        element = stack.pop()
        if skip is not None and skip(element):
            continue
        yield element
        stack.extend(reversed([c for c in element.children if isinstance(c, Element)]))


def is_metadata(element: Element) -> bool:
    return element.is_svg("metadata")


def iter_nodes(doc: Document) -> Iterator[tuple[Node, Element | None]]:
    """Every node in document order, with its parent element (None for the
    root and the prolog/epilog nodes). No recursion."""
    for node in doc.prolog:
        yield node, None
    stack: list[tuple[Node, Element | None]] = [(doc.root, None)]
    while stack:
        node, parent = stack.pop()
        yield node, parent
        if isinstance(node, Element):
            stack.extend((child, node) for child in reversed(node.children))
    for node in doc.epilog:
        yield node, None


def text_of(element: Element) -> str:
    parts = []
    for e in iter_elements(element):
        parts += [c.value for c in e.children if isinstance(c, Text)]
    return "".join(parts)


def escape_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
            .replace("\t", "&#9;").replace("\n", "&#10;").replace("\r", "&#13;"))


@dataclass
class Writer:
    """Writes nodes as XML. ``keep_node`` decides which nodes are written and
    ``attributes`` which attributes of an element are."""
    keep_node: Callable[[Node], bool] = lambda node: True
    attributes: Callable[[Element], list[tuple[str, str]]] = lambda element: element.attrs

    def node(self, node: Node, out: list[str], extra_ns: list[tuple[str, str]] = ()) -> None:
        if isinstance(node, Text):
            if node.cdata:
                out.append("<![CDATA[" + node.value.replace("]]>", "]]]]><![CDATA[>") + "]]>")
            else:
                out.append(escape_text(node.value))
        elif isinstance(node, Comment):
            out.append(f"<!--{node.value}-->")
        elif isinstance(node, PI):
            out.append(f"<?{node.target}{' ' + node.data if node.data else ''}?>")
        else:
            self.element(node, out, extra_ns)

    def element(self, element: Element, out: list[str],
                extra_ns: list[tuple[str, str]] = ()) -> None:
        attrs = list(extra_ns) + self.attributes(element)
        out.append("<" + element.name + "".join(f' {k}="{escape_attr(v)}"' for k, v in attrs))
        children = [c for c in element.children if self.keep_node(c)]
        if not children:
            out.append("/>")
            return
        out.append(">")
        for child in children:
            self.node(child, out)
        out.append(f"</{element.name}>")

    def document(self, doc: Document) -> str:
        out: list[str] = []
        if doc.declaration is not None:
            version, encoding, standalone = doc.declaration
            decl = f'<?xml version="{version}"'
            if encoding:
                decl += f' encoding="{encoding}"'
            if standalone != -1:
                decl += f' standalone="{"yes" if standalone else "no"}"'
            out.append(decl + "?>\n")
        for node in doc.prolog:
            if self.keep_node(node):
                self.node(node, out)
                out.append("\n")
        self.element(doc.root, out)
        out.append("\n")
        for node in doc.epilog:
            if self.keep_node(node):
                self.node(node, out)
                out.append("\n")
        return "".join(out)


def standalone_xml(element: Element) -> bytes:
    """An element and its contents as a standalone XML document, with the
    namespace declarations it inherits added to it."""
    declared = {k for k, _v in element.attrs if is_namespace_declaration(k)}
    extra = []
    for prefix, uri in element.ns.items():
        name = "xmlns" if prefix == "" else f"xmlns:{prefix}"
        if prefix != "xml" and name not in declared:
            extra.append((name, uri))
    out: list[str] = []
    Writer().element(element, out, extra)
    return "".join(out).encode("utf-8")


# -- sections ---------------------------------------------------------------------------


def _limited(section: Section, total: int) -> None:
    if total > MAX_LISTED:
        section.add("more", "More", f"{total - MAX_LISTED} more not listed")


def metadata_sections(doc: Document, warnings: list[str]) -> list[Section]:
    """<metadata> elements: RDF / Dublin Core properties (read with core/xmp)."""
    section = Section("svg_metadata", "Metadata")
    elements = [e for e in iter_elements(doc.root) if e.is_svg("metadata")]
    for i, element in enumerate(elements, 1):
        label = "metadata element" if i == 1 else f"metadata element {i}"
        rdf = next((e for e in iter_elements(element) if e.local == "RDF" and e.uri == RDF_NS),
                   None)
        others = [c.name for c in element.children
                  if isinstance(c, Element) and (rdf is None or rdf not in iter_elements(c))]
        if rdf is not None:
            xmp.add_to_section(section, standalone_xml(rdf), label, "", warnings)
            section.get(f"{label}.size").note = f"<metadata> at line {element.line}"
            if others:
                section.add(f"{label}.other", f"{label}: other contents",
                            ", ".join(f"<{n}>" for n in others[:20]), sensitive=True)
            continue
        inner = [e.name for e in iter_elements(element)][1:]
        text = " ".join(text_of(element).split())
        section.add(f"{label}.contents", label,
                    (", ".join(f"<{n}>" for n in inner[:20]) or "(empty)")
                    + (f"; text: {text[:500]}" if text else ""),
                    sensitive=bool(inner or text), note=f"<metadata> at line {element.line}")
    return [section] if section.fields else []


def title_desc_sections(doc: Document) -> list[Section]:
    """<title> and <desc>: accessibility text, often the document's name."""
    section = Section("title_desc", "Title and description")
    found = [e for e in iter_elements(doc.root) if e.is_svg("title") or e.is_svg("desc")]
    for i, element in enumerate(found[:MAX_LISTED], 1):
        label = "Title" if element.local == "title" else "Description"
        where = element.parent.name if element.parent is not None else "?"
        section.add(f"{element.local}_{i}", label, " ".join(text_of(element).split()),
                    sensitive=True, note=f"<{element.local}> in <{where}> at line {element.line}")
    _limited(section, len(found))
    return [section] if section.fields else []


def comment_sections(doc: Document) -> list[Section]:
    section = Section("comments", "Comments")
    comments = [n for n, _parent in iter_nodes(doc) if isinstance(n, Comment)]
    for i, comment in enumerate(comments[:MAX_LISTED], 1):
        section.add(f"comment_{i}", "Comment", comment.value.strip(), sensitive=True,
                    note=f"line {comment.line}")
    _limited(section, len(comments))
    return [section] if section.fields else []


def pi_sections(doc: Document) -> list[Section]:
    """Processing instructions (the XML declaration is not one)."""
    section = Section("processing_instructions", "Processing instructions")
    pis = [n for n, _parent in iter_nodes(doc) if isinstance(n, PI)]
    for i, pi in enumerate(pis[:MAX_LISTED], 1):
        section.add(f"pi_{i}", pi.target, pi.data, sensitive=True, note=f"line {pi.line}")
    _limited(section, len(pis))
    return [section] if section.fields else []


def editor_sections(doc: Document) -> list[Section]:
    """Namespaces, elements and attributes of drawing programs."""
    section = Section("editor", "Editor data")
    declared: dict[tuple[str, str], int] = {}
    elements: list[Element] = []
    attributes: list[tuple[Element, str, str]] = []
    # <metadata> is reported (and removed) as a whole, so it is skipped here.
    for element in iter_elements(doc.root, skip=is_metadata):
        for name, value in element.attrs:
            if is_namespace_declaration(name):
                prefix = name[6:] if name != "xmlns" else ""
                if editor_tool(value):
                    declared.setdefault((prefix, value), element.line)
            elif editor_tool(element.attr_uri(name)):
                attributes.append((element, name, value))
        if editor_tool(element.uri):
            elements.append(element)
    for (prefix, uri), line in declared.items():
        n_el = sum(1 for e in elements if e.uri == uri)
        n_at = sum(1 for e, name, _v in attributes if e.attr_uri(name) == uri)
        section.add(f"xmlns:{prefix}@{line}", f"{editor_tool(uri)} namespace",
                    f"{prefix or '(default)'} = {uri}", sensitive=True,
                    note=f"{n_el} elements, {n_at} attributes")
    listed = 0
    for element in elements:
        if listed < MAX_LISTED and (element.parent is None
                                    or not editor_tool(element.parent.uri)):
            section.add(f"<{element.name}>@{element.line}", f"<{element.name}>",
                        f"{len(element.attrs)} attributes, {len(element.children)} children",
                        sensitive=True, note=f"line {element.line}")
            listed += 1
    for element, name, value in attributes:
        if listed < MAX_LISTED:
            section.add(f"{name}@{element.line}", name, value, sensitive=True,
                        note=f"on <{element.name}> at line {element.line}")
        listed += 1
    _limited(section, listed)
    return [section] if section.fields else []


_PATH_START = r"""(?:^|(?<=[\s"'(,;=]))"""
_LOCAL_PATH = re.compile(_PATH_START + r"""(
      file:/[^\s"')]*
    | [A-Za-z]:[\\/][^"')]*
    | \\\\[^\\\s"')]+\\[^"')]*
    | /(?:home|Users|root|var/folders|private/var|mnt|media|tmp)/[^"')]*
    )""", re.VERBOSE)
_USER_NAME = re.compile(r"(?i)(?:[a-z]:[\\/]+(?:users|documents and settings)[\\/]+|"
                        r"/home/|/users/)([^\\/\"']+)")


def local_paths(value: str) -> list[str]:
    """Absolute local file paths (Windows, UNC, Unix home folders, file: URLs)."""
    return [m.group(1).rstrip() for m in _LOCAL_PATH.finditer(value)]


def user_name(path: str) -> str | None:
    m = _USER_NAME.search(urllib.parse.unquote(path))
    return m.group(1) if m else None


def path_attributes(doc: Document) -> list[tuple[Element, str, str, list[str]]]:
    """(element, attribute, value, paths) for every attribute holding a local path."""
    out = []
    for element in iter_elements(doc.root):
        for name, value in element.attrs:
            if is_namespace_declaration(name) or value.lstrip()[:5].lower() == "data:":
                continue
            paths = local_paths(value)
            if paths:
                out.append((element, name, value, paths))
    return out


def local_path_sections(doc: Document, warnings: list[str]) -> list[Section]:
    """Absolute local file paths in attributes: they reveal user names and folders."""
    section = Section("local_paths", "Local file paths")
    found = path_attributes(doc)
    users = set()
    for element, name, _value, paths in found[:MAX_LISTED]:
        for path in paths:
            user = user_name(path)
            if user:
                users.add(user)
            section.add(f"{name}@{element.line}", f"{name} on <{element.name}>", path,
                        sensitive=True, note=f"line {element.line}"
                        + (f"; reveals the user name {user!r}" if user else ""))
    _limited(section, len(found))
    if found:
        who = f" (user names: {', '.join(sorted(users))})" if users else ""
        warnings.append(f"CRITICAL: {len(found)} attribute(s) contain absolute local file "
                        f"paths{who}")
    return [section] if section.fields else []


# -- embedded data: URIs ---------------------------------------------------------------


class _LimitReached(Exception):
    pass


def decode_data_uri(uri: str, limit: int) -> tuple[str, bytes]:
    """(MIME type, decoded bytes) of a data: URI; raises MalformedFileError, or
    _LimitReached if it would decode to more than ``limit`` bytes."""
    header, sep, payload = uri.strip()[5:].partition(",")
    if not sep:
        raise MalformedFileError("data: URI without a comma")
    parts = header.split(";")
    mime = parts[0].strip().lower() or "text/plain"
    if any(p.strip().lower() == "base64" for p in parts[1:]):
        cleaned = re.sub(r"\s+", "", payload)
        if len(cleaned) // 4 * 3 > limit:
            raise _LimitReached
        cleaned += "=" * (-len(cleaned) % 4)
        try:
            return mime, base64.b64decode(cleaned, validate=True)
        except (binascii.Error, ValueError):
            raise MalformedFileError("data: URI holds invalid base64") from None
    if len(payload) > limit * 3:
        raise _LimitReached
    raw = urllib.parse.unquote_to_bytes(payload)
    if len(raw) > limit:
        raise _LimitReached
    return mime, raw


def is_href(element: Element, name: str) -> bool:
    """href or xlink:href (the attributes that point to images and resources)."""
    return name.split(":", 1)[-1] == "href" and element.attr_uri(name) in (None, XLINK_NS)


@dataclass
class Embedded:
    element: Element
    attribute: str
    mime: str
    data: bytes | None
    """Decoded bytes, or None if they could not be decoded or were over the limit."""
    detected: filetype.FileType | None = None
    problem: str | None = None


def embedded_resources(doc: Document) -> list[Embedded]:
    """Every data: URI in an href / xlink:href attribute, decoded (capped)."""
    out: list[Embedded] = []
    remaining = MAX_EMBEDDED_TOTAL
    for element in iter_elements(doc.root):
        for name, value in element.attrs:
            if not (is_href(element, name) and value.lstrip()[:5].lower() == "data:"):
                continue
            if len(out) >= MAX_EMBEDDED:
                out.append(Embedded(element, name, "", None,
                                    problem=f"more than {MAX_EMBEDDED} embedded resources"))
                return out
            try:
                mime, raw = decode_data_uri(value, remaining)
            except _LimitReached:
                out.append(Embedded(element, name, "", None, problem="over the "
                                    f"{MAX_EMBEDDED_TOTAL // (1024 * 1024)} MiB limit for "
                                    "embedded data; not examined"))
                continue
            except MalformedFileError as exc:
                out.append(Embedded(element, name, "", None, problem=str(exc)))
                continue
            remaining -= len(raw)
            out.append(Embedded(element, name, mime, raw, filetype.detect(raw)))
    return out


def _inner_parse(item: Embedded, depth: int) -> dict | None:
    """Parse an embedded resource with its own format's parser (None if metaex
    can't read that type); raises MalformedFileError."""
    from metaex.core.registry import format_module

    if item.detected.key == "svg":
        if depth >= MAX_NESTED_SVG:
            raise MalformedFileError(f"SVG images nested more than {MAX_NESTED_SVG} deep")
        return _parse(item.data, depth + 1)
    module = format_module(item.detected)
    return module.parse(item.data) if module is not None else None


def embedded_sections(doc: Document, warnings: list[str], depth: int = 0) -> list[Section]:
    """A list of the embedded resources, then the metadata sections of each
    image, found with its own format's parser."""
    listing = Section("embedded_images", "Embedded images")
    inner_sections: list[Section] = []
    for n, item in enumerate(embedded_resources(doc), 1):
        where = f"{item.attribute} of <{item.element.name}> at line {item.element.line}"
        name = f"Embedded image {n}"
        if item.data is None:
            listing.add(f"image_{n}", name, f"(not examined: {item.problem})", sensitive=True,
                        note=where)
            warnings.append(f"{name.lower()} ({where}) was not examined: {item.problem}")
            continue
        kind = item.detected.name if item.detected.key != filetype.UNKNOWN.key else item.mime
        try:
            parsed = _inner_parse(item, depth)
        except MalformedFileError as exc:
            parsed, item.problem = None, f"malformed: {exc}"
        if parsed is None:
            problem = item.problem or "metaex can't read this type"
            listing.add(f"image_{n}", name, f"{kind}, {len(item.data)} bytes (not examined: "
                        f"{problem})", sensitive=True, note=where)
            warnings.append(f"{name.lower()} ({kind}, {where}) was not examined for "
                            f"metadata: {problem}")
            continue
        listing.add(f"image_{n}", name, f"{kind}, {len(item.data)} bytes", note=where)
        prefix = f"{name} ({kind})"
        for w in parsed.get("warnings", []):
            if w.startswith("CRITICAL: "):
                warnings.append(f"CRITICAL: {prefix}: {w[len('CRITICAL: '):]}")
            else:
                warnings.append(f"{prefix}: {w}")
        for section in parsed.get("sections", []):
            inner_sections.append(Section(section.key, f"{prefix}: {section.title}",
                                          section.fields))
    return ([listing] if listing.fields else []) + inner_sections


# -- active content and external references (warnings) --------------------------------

_CSS_URL = re.compile(r"""url\(\s*["']?\s*([^"')\s]+)""", re.IGNORECASE)
_CSS_IMPORT = re.compile(r"""@import\s+(?:url\()?\s*["']?([^"')\s;]+)""", re.IGNORECASE)


def _is_external(url: str) -> bool:
    url = url.strip().lower()
    return url.startswith(("http://", "https://", "//", "ftp://"))


def css_sources(doc: Document) -> Iterator[tuple[Element, str]]:
    """(element, CSS text) for style attributes and <style> elements."""
    for element in iter_elements(doc.root):
        style = element.get("style")
        if style:
            yield element, style
        if element.local == "style":
            yield element, text_of(element)


def css_data_uris(doc: Document) -> list[Element]:
    """Elements whose CSS contains a data: URI (not examined by metaex)."""
    return [e for e, css in css_sources(doc)
            if any(u.lower().startswith("data:") for u in _CSS_URL.findall(css))]


def active_content_warnings(doc: Document, warnings: list[str]) -> None:
    """Scripts, event handlers and external references: not metadata, but
    worth knowing (opening the file can run code or contact a server)."""
    scripts = [e for e in iter_elements(doc.root) if e.local == "script"]
    if scripts:
        warnings.append(f"the SVG contains {len(scripts)} <script> element(s) (first at line "
                        f"{scripts[0].line}); they can run code when the file is opened in a "
                        "browser")
    handlers = [(e, n) for e in iter_elements(doc.root) for n, _v in e.attrs
                if ":" not in n and n.lower().startswith("on")]
    if handlers:
        e, n = handlers[0]
        warnings.append(f"the SVG has {len(handlers)} event handler attribute(s) (e.g. {n} on "
                        f"<{e.name}> at line {e.line}); they can run code")
    refs: list[str] = []
    for element in iter_elements(doc.root):
        for name, value in element.attrs:
            if (is_href(element, name) or name == "src") and _is_external(value):
                refs.append(f"{value.strip()} ({name} of <{element.name}> at line "
                            f"{element.line})")
            elif is_href(element, name) and value.strip().lower().startswith("javascript:"):
                warnings.append(f"javascript: link on <{element.name}> at line {element.line}")
    for element, css in css_sources(doc):
        for url in dict.fromkeys(_CSS_URL.findall(css) + _CSS_IMPORT.findall(css)):
            if _is_external(url):
                refs.append(f"{url} (CSS of <{element.name}> at line {element.line})")
    for ref in refs[:MAX_REFERENCE_WARNINGS]:
        warnings.append(f"external reference: {ref}; opening the SVG can contact that server, "
                        "which can be used for tracking")
    if len(refs) > MAX_REFERENCE_WARNINGS:
        warnings.append(f"... and {len(refs) - MAX_REFERENCE_WARNINGS} more external references")
    for element in css_data_uris(doc):
        warnings.append(f"CSS of <{element.name}> at line {element.line} contains a data: URI; "
                        "its contents were not examined")


def doctype_sections(doc: Document) -> list[Section]:
    """The standard SVG DOCTYPE: harmless (its DTD is never loaded and it
    doesn't change the drawing), listed for completeness; strip removes it."""
    if doc.doctype is None:
        return []
    version, public_id, system_id, line = doc.doctype
    section = Section("doctype", "Document type")
    section.add("doctype", "DOCTYPE", f"{version} ({public_id}, {system_id})",
                note=f"line {line}; standard W3C DTD, never loaded")
    return [section]


def structure_section(doc: Document) -> Section:
    section = Section("structure", "SVG structure")
    if doc.declaration is not None:
        version, encoding, _standalone = doc.declaration
        section.add("declaration", "XML declaration",
                    f"version {version}" + (f", encoding {encoding}" if encoding else ""))
    section.add("root", "Root element", f"<{doc.root.name}>")
    section.add("elements", "Elements", doc.element_count)
    for name, value in doc.root.attrs:
        if is_namespace_declaration(name):
            section.add(name, name, value)
    if doc.root.uri != SVG_NS:
        section.add("namespace", "Namespace", "the root element is not in the SVG namespace")
    return section


def _parse(data: bytes, depth: int) -> dict:
    """parse() for an SVG nested ``depth`` levels deep in another SVG."""
    doc = parse_document(data)
    warnings: list[str] = []
    if doc.root.uri != SVG_NS:
        warnings.append("the root element is not in the SVG namespace "
                        f"({doc.root.uri or 'none'}); viewers may not display it")
    sections: list[Section] = []
    sections += doctype_sections(doc)
    sections += metadata_sections(doc, warnings)
    sections += title_desc_sections(doc)
    sections += comment_sections(doc)
    sections += pi_sections(doc)
    sections += editor_sections(doc)
    sections += local_path_sections(doc, warnings)
    sections += embedded_sections(doc, warnings, depth)
    active_content_warnings(doc, warnings)
    return {"warnings": warnings, "sections": sections, "structure": structure_section(doc),
            "complete": True}


# -- Stripping ---------------------------------------------------------------------

TEXT_CONTEXT = {"text", "style", "script", "title", "desc", "foreignObject"}
"""Inside these elements whitespace can matter, so it is never tidied away."""
MAX_LISTED_PATHS = 5


def in_text_context(element: Element | None) -> bool:
    while element is not None:
        if element.local in TEXT_CONTEXT:
            return True
        element = element.parent
    return False


def dropped_by_strip(node: Node, paranoid: bool) -> bool:
    """True for the nodes strip() removes, with their contents: comments,
    processing instructions, <metadata>, elements in editor namespaces and,
    with ``paranoid``, <title> and <desc>."""
    if isinstance(node, (Comment, PI)):
        return True
    if isinstance(node, Element):
        if node.is_svg("metadata") or editor_tool(node.uri):
            return True
        if paranoid and (node.is_svg("title") or node.is_svg("desc")):
            return True
    return False


def _drop_plan(doc: Document, paranoid: bool) -> set[int]:
    """ids of the nodes to leave out: the dropped ones, and the whitespace
    that indented them (outside text, where it doesn't render)."""
    drop: set[int] = set()
    for node in doc.prolog + doc.epilog:
        if dropped_by_strip(node, paranoid):
            drop.add(id(node))
    for element in iter_elements(doc.root, skip=lambda e: id(e) in drop):
        previous = None
        for child in element.children:
            if dropped_by_strip(child, paranoid):
                drop.add(id(child))
                if (isinstance(previous, Text) and not previous.cdata
                        and not previous.value.strip() and not in_text_context(element)):
                    drop.add(id(previous))
            previous = child
    return drop


def _kept_attributes(element: Element) -> list[tuple[str, str]]:
    """Attributes outside editor namespaces (declarations are decided later)."""
    return [(name, value) for name, value in element.attrs
            if is_namespace_declaration(name) or not editor_tool(element.attr_uri(name))]


def _strip_embedded(item: Embedded, n: int, keep_orientation: bool, paranoid: bool,
                    depth: int) -> str:
    """The embedded image stripped with its own format's rules, as a new
    base64 data: URI; raises CannotCleanError if it can't be."""
    from metaex.core.errors import CannotCleanError, MetaexError
    from metaex.core.registry import can_strip, format_module

    where = f"{item.attribute} of <{item.element.name}> at line {item.element.line}"
    if item.data is None:
        raise CannotCleanError(f"embedded image {n} ({where}) can't be checked: {item.problem}")
    kind = item.detected.name if item.detected.key != filetype.UNKNOWN.key else (
        item.mime or "unknown data")
    try:
        if item.detected.key == "svg":
            if depth >= MAX_NESTED_SVG:
                raise CannotCleanError(f"SVG images nested more than {MAX_NESTED_SVG} deep")
            cleaned = _strip(item.data, keep_orientation, paranoid, depth + 1)
        else:
            module = format_module(item.detected)
            if not can_strip(module):
                raise CannotCleanError(f"metaex can't strip {kind} files")
            cleaned = module.strip(item.data, keep_orientation=keep_orientation,
                                   paranoid=paranoid)
    except MetaexError as exc:
        raise CannotCleanError(f"embedded image {n} ({kind}, {where}) can't be stripped: "
                               f"{exc}") from None
    return f"data:{item.detected.mime};base64," + base64.b64encode(cleaned).decode("ascii")


def strip(data: bytes | bytearray | memoryview, *, keep_orientation: bool = True,
          paranoid: bool = False) -> bytes:
    """Return a copy of the SVG with all metadata removed.

    SVG is text, so the copy is written again from the parsed document
    rather than copied byte-for-byte: every element, attribute and text that
    is kept comes out as parsed, and prefixes (the default SVG namespace,
    xlink:) stay exactly as written. Removed: the standard DOCTYPE (it has
    no effect on the drawing), <metadata>, comments, processing
    instructions (the XML declaration stays), every element and
    attribute in an editor namespace (sodipodi:namedview, inkscape:*,
    Illustrator i:*, ...) and the declarations of those namespaces and of
    any other namespace no longer used. <title> and <desc> are kept (they
    are for accessibility) unless ``paranoid``. Embedded data: URI images
    are stripped with their own format's rules (``keep_orientation`` and
    ``paranoid`` apply to them) and written back as base64.

    Refused, with nothing written: files parse() refuses, embedded data
    that can't be stripped (an unsupported or damaged image, data in CSS),
    and local file paths in attributes that are kept (an image linked from
    C:\\Users\\<name>\\... can't be removed without breaking the drawing).
    """
    return _strip(bytes(data), keep_orientation, paranoid, 0)


def _strip(data: bytes, keep_orientation: bool, paranoid: bool, depth: int) -> bytes:
    from metaex.core.errors import CannotCleanError

    doc = parse_document(data)
    drop = _drop_plan(doc, paranoid)
    kept = [e for e in iter_elements(doc.root, skip=lambda e: id(e) in drop)]
    kept_ids = {id(e) for e in kept}
    css = [e for e in css_data_uris(doc) if id(e) in kept_ids]
    if css:
        raise CannotCleanError(f"the CSS of <{css[0].name}> at line {css[0].line} contains a "
                               "data: URI; metaex can't check what it holds")

    attrs = {id(e): _kept_attributes(e) for e in kept}
    resources = [r for r in embedded_resources(doc) if id(r.element) in kept_ids]
    for n, item in enumerate(resources, 1):
        new = _strip_embedded(item, n, keep_orientation, paranoid, depth)
        attrs[id(item.element)] = [(k, new if k == item.attribute else v)
                                   for k, v in attrs[id(item.element)]]

    paths = [(e, name, path) for e in kept for name, value in attrs[id(e)]
             if not is_namespace_declaration(name) and value.lstrip()[:5].lower() != "data:"
             for path in local_paths(value)]
    if paths:
        listed = "; ".join(f"{path} ({name} of <{e.name}> at line {e.line})"
                           for e, name, path in paths[:MAX_LISTED_PATHS])
        more = f" and {len(paths) - MAX_LISTED_PATHS} more" if len(paths) > MAX_LISTED_PATHS else ""
        raise CannotCleanError(
            f"the drawing uses local files: {listed}{more}. These paths reveal user names and "
            "folders, but removing them would break the drawing; embed or move the files "
            "and try again")

    # Namespace declarations: drop editor namespaces and any prefix no longer
    # used; the default namespace and xlink on the root stay as written.
    used = {""}
    for e in kept:
        used.add(e.prefix)
        used.update(name.split(":", 1)[0] for name, _v in attrs[id(e)]
                    if ":" in name and not is_namespace_declaration(name))
    for e in kept:
        def keep(name: str, value: str, e: Element = e) -> bool:
            if not is_namespace_declaration(name):
                return True
            if editor_tool(value):
                return False
            prefix = name[6:] if name != "xmlns" else ""
            return prefix in used or (e is doc.root and name in ("xmlns", "xmlns:xlink"))
        attrs[id(e)] = [(k, v) for k, v in attrs[id(e)] if keep(k, v)]

    writer = Writer(keep_node=lambda node: id(node) not in drop,
                    attributes=lambda element: attrs[id(element)])
    cleaned = writer.document(doc).encode("utf-8")
    # Self-check: the output is accepted and draws exactly the same.
    if drawing(parse_document(cleaned), depth) != drawing(doc, depth):
        raise MalformedFileError("internal check failed: the drawing changed while stripping")
    return cleaned


def kept_warnings(cleaned: bytes) -> list[str]:
    """What a cleaned SVG still contains that the user may want to know about:
    <title>/<desc> text, scripts, event handlers and external references."""
    doc = parse_document(cleaned)
    out: list[str] = []
    texts = [e for e in iter_elements(doc.root) if e.is_svg("title") or e.is_svg("desc")]
    if texts:
        sample = " ".join(text_of(texts[0]).split())[:80]
        out.append(f"kept {len(texts)} <title>/<desc> element(s) for accessibility (e.g. "
                   f"{sample!r}); use --paranoid to remove them")
    active: list[str] = []
    active_content_warnings(doc, active)
    out += active
    return out


# -- Verification -------------------------------------------------------------------


def _kept_by_design() -> dict[str, set[str] | None]:
    """<title>/<desc> and the list of embedded images, plus whatever the
    embedded images' own formats keep by design (their sections appear in
    the SVG's report)."""
    import importlib

    kept: dict[str, set[str] | None] = {"title_desc": None, "embedded_images": None}
    for name in ("jpeg", "png", "gif", "webp"):
        module = importlib.import_module(f"metaex.formats.{name}")
        for key, fields in getattr(module, "KEPT_BY_DESIGN", {}).items():
            if fields is None or kept.get(key, set()) is None:
                kept[key] = None
            else:
                kept[key] = kept.get(key, set()) | fields
    return kept


KEPT_BY_DESIGN: dict[str, set[str] | None] = _kept_by_design()
IMAGE_LABEL = "Drawing"
"""What verify compares for SVG: the canonical drawing, not bytes."""


def _embedded_digest(value: str, depth: int) -> str:
    """Stands for an embedded data: URI in the canonical drawing: the SHA-256 of
    its image data (so stripping its metadata doesn't change the drawing)."""
    import hashlib

    from metaex.core.registry import format_module

    try:
        _mime, raw = decode_data_uri(value, MAX_EMBEDDED_TOTAL)
    except (MalformedFileError, _LimitReached):
        return "unreadable:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
    detected = filetype.detect(raw)
    try:
        if detected.key == "svg":
            if depth >= MAX_NESTED_SVG:
                raise MalformedFileError("nested too deeply")
            return "svg:" + hashlib.sha256(drawing(parse_document(raw), depth + 1)).hexdigest()
        module = format_module(detected)
        image_data = getattr(module, "image_data", None)
        if callable(image_data):
            return f"{detected.key}:" + hashlib.sha256(image_data(raw)).hexdigest()
    except MalformedFileError:
        pass
    return "unreadable:" + hashlib.sha256(raw).hexdigest()


def drawing(doc: Document, depth: int = 0) -> bytes:
    """A canonical form of what is drawn, for comparing an original and its
    cleaned copy: elements and attributes by namespace URI (not prefix),
    attributes sorted, without what strip() removes (comments, processing
    instructions, <metadata>, editor data, namespace declarations) and
    without <title>/<desc>; text merged where removed nodes split it, and
    whitespace collapsed where it doesn't render (outside text elements);
    embedded images replaced by the SHA-256 of their image data."""
    out: list[str] = []
    # Iterative walk: ("open", element) then its children, then ("close",).
    work: list = [("open", doc.root)]
    while work:
        item = work.pop()
        if item[0] == "close":
            out.append(">")
            continue
        if item[0] == "text":
            out.append("T" + repr(item[1]))
            continue
        element = item[1]
        if dropped_by_strip(element, True):
            continue
        attrs = []
        for name, value in element.attrs:
            uri = element.attr_uri(name)
            if is_namespace_declaration(name) or editor_tool(uri):
                continue
            if is_href(element, name) and value.lstrip()[:5].lower() == "data:":
                value = "embedded:" + _embedded_digest(value, depth)
            attrs.append((uri or "", name.split(":", 1)[-1], value))
        out.append("<" + repr((element.uri or "", element.local, sorted(attrs))))
        children: list = []
        buffer = ""
        text_context = in_text_context(element)

        def flush(text: str) -> None:
            # Outside text, whitespace doesn't render: compare it collapsed.
            if not text_context:
                text = " ".join(text.split())
            if text:
                children.append(("text", text))

        for child in element.children:
            if isinstance(child, Text):
                buffer += child.value
            elif isinstance(child, Element) and not dropped_by_strip(child, True):
                flush(buffer)
                buffer = ""
                children.append(("open", child))
        flush(buffer)
        work.append(("close",))
        work.extend(reversed(children))
    return "".join(out).encode("utf-8")


def _structure_leftovers(doc: Document) -> list[tuple[str, str, str]]:
    """Namespace declarations strip() would not have kept."""
    used = {""}
    for e in iter_elements(doc.root):
        used.add(e.prefix)
        used.update(n.split(":", 1)[0] for n, _v in e.attrs
                    if ":" in n and not is_namespace_declaration(n))
    out = []
    for e in iter_elements(doc.root):
        for name, value in e.attrs:
            if not is_namespace_declaration(name) or editor_tool(value):
                continue  # editor namespaces are reported in the Editor data section
            prefix = name[6:] if name != "xmlns" else ""
            if prefix not in used and not (e is doc.root and name == "xmlns:xlink"):
                out.append(("SVG structure", f"{name} on <{e.name}> at line {e.line}",
                            f"unused namespace declaration ({value})"))
    return out


def _details(cleaned: bytes, original: bytes | None, depth: int) -> dict:
    import hashlib

    from metaex.core.registry import format_module

    out: dict = {"leftovers": [], "problems": [], "image_sha256": None,
                 "original_image_sha256": None, "image_data_match": None,
                 "image_label": IMAGE_LABEL}
    try:
        doc = parse_document(cleaned)
    except MalformedFileError as exc:
        out["problems"].append(f"not an SVG metaex accepts: {exc}")
        return out
    out["leftovers"] += _structure_leftovers(doc)
    for element in css_data_uris(doc):
        out["problems"].append(f"CSS of <{element.name}> at line {element.line} contains a "
                               "data: URI that can't be checked")
    # Each embedded image gets its own format's structural checks (its
    # metadata sections are already part of the SVG's report).
    for n, item in enumerate(embedded_resources(doc), 1):
        name = f"Embedded image {n}"
        if item.data is None:
            out["problems"].append(f"{name.lower()} can't be checked: {item.problem}")
            continue
        if item.detected.key == "svg":
            if depth >= MAX_NESTED_SVG:
                out["problems"].append(f"{name.lower()}: SVG nested too deeply to check")
                continue
            inner = _details(item.data, None, depth + 1)
        else:
            module = format_module(item.detected)
            hook = getattr(module, "verify_details", None)
            if not callable(hook):
                out["problems"].append(f"{name.lower()} ({item.detected.name}) can't be "
                                       "checked: metaex can't verify this type")
                continue
            inner = hook(item.data, None)
        kind = item.detected.name
        out["leftovers"] += [(f"{name} ({kind}): {s}", label, value)
                             for s, label, value in inner["leftovers"]]
        out["problems"] += [f"{name.lower()} ({kind}): {p}" for p in inner["problems"]]

    out["image_sha256"] = hashlib.sha256(drawing(doc, depth)).hexdigest()
    if original is not None:
        try:
            original_doc = parse_document(original)
        except MalformedFileError as exc:
            out["problems"].append(f"original is not an SVG metaex accepts ({exc}); the "
                                   "drawing was not compared")
        else:
            theirs = hashlib.sha256(drawing(original_doc, depth)).hexdigest()
            out["original_image_sha256"] = theirs
            out["image_data_match"] = theirs == out["image_sha256"]
            if theirs != out["image_sha256"]:
                out["problems"].append("the drawing differs from the original")
    return out


def verify_details(cleaned: bytes, original: bytes | None = None) -> dict:
    """SVG-specific verification, beyond the section-level whitelist.

    - The file must be an SVG metaex accepts (UTF-8, well-formed, no DOCTYPE
      other than the standard SVG one). A DOCTYPE left in a cleaned file
      is a leftover (strip removes it).
    - Leftovers: unused namespace declarations; comments, processing
      instructions, <metadata>, editor data and local paths are reported by
      parse() and are leftovers too.
    - Each embedded image is checked with its own format's structural
      checks (chunk/segment whitelists, hidden data).
    - The canonical drawing (see drawing()) is hashed and compared with the
      original's if one is given: equal means nothing that is drawn changed.
    """
    return _details(bytes(cleaned), None if original is None else bytes(original), 0)


# -- parse -------------------------------------------------------------------------


def parse(data: bytes | bytearray | memoryview) -> dict:
    """Parse an SVG and return its warnings, report sections and structure.

    Raises MalformedFileError if the file is refused (ENTITY declaration,
    a DOCTYPE other than the standard SVG 1.0 / 1.1 one, not UTF-8, too
    large) or is not well-formed XML with an <svg> root.
    """
    return _parse(bytes(data), 0)
