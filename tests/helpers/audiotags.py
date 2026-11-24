"""Build the tags found at the end of audio files: ID3v1 (and "TAG+"),
APEv1/APEv2 and Lyrics3 v1/v2. All values are made up."""

from __future__ import annotations


def _field(text: str | bytes, size: int) -> bytes:
    raw = text.encode("latin-1") if isinstance(text, str) else text
    assert len(raw) <= size
    return raw + bytes(size - len(raw))


def id3v1(title: str = "Floor plan blues", artist: str = "Jane Doe", album: str = "Demo tapes",
          year: str = "2024", comment: str | bytes = "jane.doe@example.com",
          track: int | None = 3, genre: int = 13) -> bytes:
    """A 128-byte ID3v1 tag (ID3v1.1 when ``track`` is given)."""
    if track is not None:
        comment_field = _field(comment, 28) + b"\x00" + bytes([track])
    else:
        comment_field = _field(comment, 30)
    return (b"TAG" + _field(title, 30) + _field(artist, 30) + _field(album, 30)
            + _field(year, 4) + comment_field + bytes([genre]))


def tag_plus(title: str = "", artist: str = "", album: str = "", genre: str = "Bedroom pop",
             start: str = "000:05", end: str = "003:10", speed: int = 2) -> bytes:
    """The 227-byte "TAG+" block that goes right before an ID3v1 tag: the
    title, artist and album continue those of the ID3v1 tag."""
    return (b"TAG+" + _field(title, 60) + _field(artist, 60) + _field(album, 60)
            + bytes([speed]) + _field(genre, 30) + _field(start, 6) + _field(end, 6))


def ape_item(key: str, value: bytes, kind: int = 0) -> bytes:
    return (len(value).to_bytes(4, "little") + (kind << 1).to_bytes(4, "little")
            + key.encode("ascii") + b"\x00" + value)


def ape(items: list[bytes], version: int = 2000, header: bool = True) -> bytes:
    """An APE tag: header (APEv2, optional), items, footer."""
    body = b"".join(items)
    size = len(body) + 32
    flags = (1 << 31) if header and version >= 2000 else 0

    def block(is_header: bool) -> bytes:
        f = flags | ((1 << 29) if is_header else 0)
        return (b"APETAGEX" + version.to_bytes(4, "little") + size.to_bytes(4, "little")
                + len(items).to_bytes(4, "little") + f.to_bytes(4, "little") + bytes(8))

    return (block(True) if flags else b"") + body + block(False)


def ape_kitchen_sink(cover: bytes) -> bytes:
    return ape([
        ape_item("Title", "Floor plan blues".encode()),
        ape_item("Artist", "Jane Doe".encode()),
        ape_item("Comment", "Write to jane.doe@example.com".encode()),
        ape_item("Year", b"2024"),
        ape_item("Tool Name", b"Example Tagger 1.0"),
        ape_item("Record Location", "12 Example Street".encode()),
        ape_item("Related", b"https://example.com/jane", kind=2),
        ape_item("Cover Art (Front)", b"cover.jpg\x00" + cover, kind=1),
    ])


def lyrics3_v2(fields: list[tuple[str, str]] | None = None) -> bytes:
    if fields is None:
        fields = [("IND", "10"), ("LYR", "[00:01]La la la\r\n[00:02]the kitchen is small"),
                  ("AUT", "Jane Doe"), ("EAR", "Jane Doe"), ("ETT", "Floor plan blues")]
    body = b"LYRICSBEGIN" + b"".join(
        k.encode("latin-1") + f"{len(v.encode('latin-1')):05d}".encode() + v.encode("latin-1")
        for k, v in fields)
    return body + f"{len(body):06d}".encode() + b"LYRICS200"


def lyrics3_v1(text: str = "La la la, the kitchen is small") -> bytes:
    return b"LYRICSBEGIN" + text.encode("latin-1") + b"LYRICSEND"
