"""Build SVG files for tests, in the styles real editors write them."""

from __future__ import annotations

import base64

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
SODIPODI_NS = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"


def data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def simple_svg(body: str = '<rect x="1" y="1" width="8" height="8" fill="red"/>',
               attrs: str = "", prolog: str = "") -> bytes:
    """A minimal SVG with the default and xlink namespaces."""
    return (f'{prolog}<svg xmlns="{SVG_NS}" xmlns:xlink="{XLINK_NS}" width="10" height="10"'
            f'{(" " + attrs) if attrs else ""}>{body}</svg>\n').encode("utf-8")


def sample_jpeg() -> bytes:
    """A small JPEG with EXIF (GPS, camera, serial number) and XMP."""
    from helpers.jpeg import sample_photo

    return sample_photo()


def inkscape_svg(*, user: str = "jane", image: bytes | None = None,
                 image_mime: str = "image/jpeg", extra: str = "") -> bytes:
    """An SVG as Inkscape saves it: sodipodi:namedview, inkscape:/sodipodi:
    attributes (with absolute paths), RDF metadata, a title and a layer.
    ``image`` is embedded as a data: URI."""
    if image is None:
        image = sample_jpeg()
    href = data_uri(image, image_mime)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!-- Created with Inkscape (http://www.inkscape.org/) -->

<svg
   width="210mm"
   height="297mm"
   viewBox="0 0 210 297"
   version="1.1"
   id="svg5"
   inkscape:version="1.2.2 (b0a8486541, 2022-12-01)"
   sodipodi:docname="floor plan.svg"
   inkscape:export-filename="/home/{user}/Pictures/floor plan.png"
   inkscape:export-xdpi="96"
   inkscape:export-ydpi="96"
   xmlns:inkscape="{INKSCAPE_NS}"
   xmlns:sodipodi="{SODIPODI_NS}"
   xmlns:xlink="{XLINK_NS}"
   xmlns="{SVG_NS}"
   xmlns:svg="{SVG_NS}"
   xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
   xmlns:cc="http://creativecommons.org/ns#"
   xmlns:dc="http://purl.org/dc/elements/1.1/">
  <title
     id="title1">Floor plan for {user}</title>
  <sodipodi:namedview
     id="namedview7"
     pagecolor="#ffffff"
     bordercolor="#666666"
     inkscape:zoom="0.7"
     inkscape:cx="396.4"
     inkscape:window-width="1920"
     inkscape:window-height="1016"
     inkscape:current-layer="layer1" />
  <defs
     id="defs2">
    <inkscape:path-effect
       effect="spiro"
       id="path-effect1"
       is_visible="true" />
    <linearGradient id="grad1"><stop offset="0" style="stop-color:#000000"/></linearGradient>
  </defs>
  <metadata
     id="metadata1">
    <rdf:RDF>
      <cc:Work
         rdf:about="">
        <dc:format>image/svg+xml</dc:format>
        <dc:title>Floor plan</dc:title>
        <dc:creator>
          <cc:Agent>
            <dc:title>Jane Doe</dc:title>
          </cc:Agent>
        </dc:creator>
        <dc:date>2024-05-01</dc:date>
        <dc:rights>
          <cc:Agent>
            <dc:title>(c) Jane Doe</dc:title>
          </cc:Agent>
        </dc:rights>
        <dc:publisher>
          <cc:Agent>
            <dc:title>Doe Design</dc:title>
          </cc:Agent>
        </dc:publisher>
      </cc:Work>
    </rdf:RDF>
  </metadata>
  <g
     inkscape:label="Layer 1"
     inkscape:groupmode="layer"
     id="layer1">
    <rect
       style="fill:#ff0000;stroke:url(#grad1)"
       id="rect1"
       width="50"
       height="40"
       x="10"
       y="20" />
    <path
       sodipodi:type="arc"
       sodipodi:cx="10"
       inkscape:path-effect="#path-effect1"
       d="M 10,10 A 5,5 0 0 1 15,15"
       id="path1" />
    <image
       width="8"
       height="8"
       x="100"
       y="100"
       xlink:href="{href}"
       sodipodi:absref="/home/{user}/Pictures/photo.jpg"
       id="image1" />
    <text
       xml:space="preserve"
       x="5"
       y="5"
       id="text1"><tspan
         sodipodi:role="line"
         id="tspan1">Kitchen &amp; dining</tspan></text>
    <!-- TODO ask {user} about the stairs -->
  </g>{extra}
</svg>
""".encode("utf-8")


ILLUSTRATOR_XMP = """<x:xmpmeta xmlns:x="adobe:ns:meta/">
      <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
        <rdf:Description rdf:about=""
            xmlns:xmp="http://ns.adobe.com/xap/1.0/"
            xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"
            xmp:CreatorTool="Adobe Illustrator 27.5 (Windows)"
            xmp:CreateDate="2024-05-01T14:30:00+02:00"
            xmpMM:DocumentID="xmp.did:0123456789abcdef"
            xmpMM:InstanceID="xmp.iid:fedcba9876543210"/>
      </rdf:RDF>
    </x:xmpmeta>"""


def illustrator_svg(*, user: str = "Jane Doe") -> bytes:
    """An SVG as Adobe Illustrator exports it: a Generator comment, xml:space,
    Adobe namespaces (i:, x:), Save for Web data and XMP in <metadata>, and
    a linked image with a local Windows path."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!-- Generator: Adobe Illustrator 27.5.0, SVG Export Plug-In . SVG Version: 6.00 Build 0)  -->
<svg version="1.1" id="Layer_1" xmlns:x="http://ns.adobe.com/Extensibility/1.0/"
\t xmlns:i="http://ns.adobe.com/AdobeIllustrator/10.0/" xmlns="{SVG_NS}"
\t xmlns:xlink="{XLINK_NS}" x="0px" y="0px" viewBox="0 0 100 100"
\t style="enable-background:new 0 0 100 100;" xml:space="preserve">
<metadata>
\t<sfw xmlns="http://ns.adobe.com/SaveForWeb/1.0/">
\t\t<slices></slices>
\t\t<sliceSourceBounds bottomLeftOrigin="true" height="100" width="100" x="0" y="0"></sliceSourceBounds>
\t</sfw>
\t{ILLUSTRATOR_XMP}
</metadata>
<style type="text/css">
\t.st0{{fill:#00FF00;}}
</style>
<g i:extraneous="self">
\t<circle class="st0" cx="50" cy="50" r="40"/>
\t<image width="10" height="10" xlink:href="C:\\Users\\{user}\\Documents\\logo.png" transform="matrix(1 0 0 1 5 5)"/>
</g>
</svg>
""".encode("utf-8")


def kitchen_sink_svg() -> bytes:
    """An Inkscape-style SVG with everything metaex reports that can still be
    cleaned: RDF metadata, a title and description, comments, a processing
    instruction, editor data with local paths (in attributes strip removes),
    an embedded JPEG with GPS, an embedded PNG with text, a nested SVG with
    a comment, a script and an external reference. Values are made up."""
    from helpers.png import make_png, text

    png = make_png(text("Author", "Jane Doe"), text("Comment", "drawn on the train"))
    nested = simple_svg("<!-- nested: made by Jane --><circle r='2'/>")
    extra = f"""
  <desc id="desc1">Draft for the landlord, do not share</desc>
  <image href="{data_uri(png, 'image/png')}" width="4" height="4"/>
  <image href="{data_uri(nested, 'image/svg+xml')}" width="10" height="10"/>
  <script>console.log("hello")</script>
  <image href="https://tracker.example/pixel.png" width="1" height="1"/>"""
    declaration, rest = inkscape_svg(extra=extra).split(b"\n", 1)
    return declaration + b'\n<?xml-stylesheet type="text/css" href="plan.css"?>\n' + rest
