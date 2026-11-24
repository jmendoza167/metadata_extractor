"""Build ID3v2 tags (2.2, 2.3, 2.4) and their frames in memory.

All metadata is made up (Jane Doe, jane.doe@example.com).
"""

from __future__ import annotations

import zlib

LATIN1, UTF16, UTF16BE, UTF8 = 0, 1, 2, 3


def synchsafe(n: int, length: int = 4) -> bytes:
    return bytes((n >> (7 * i)) & 0x7F for i in reversed(range(length)))


def unsync(data: bytes) -> bytes:
    """Insert 0x00 after every 0xFF (always reversible)."""
    return data.replace(b"\xff", b"\xff\x00")


def encode(text: str, encoding: int = UTF8, terminate: bool = False) -> bytes:
    if encoding == LATIN1:
        raw = text.encode("latin-1")
    elif encoding == UTF16:
        raw = b"\xff\xfe" + text.encode("utf-16-le")
    elif encoding == UTF16BE:
        raw = text.encode("utf-16-be")
    else:
        raw = text.encode("utf-8")
    if terminate:
        raw += b"\x00\x00" if encoding in (UTF16, UTF16BE) else b"\x00"
    return raw


def frame(frame_id: str, payload: bytes, version: int = 4, flags: int = 0,
          plain_size: bool = False) -> bytes:
    """A frame with its header (``plain_size``: a 2.4 size that is not synchsafe)."""
    ident = frame_id.encode("ascii")
    if version == 2:
        assert len(ident) == 3
        return ident + len(payload).to_bytes(3, "big") + payload
    assert len(ident) == 4
    if version == 3 or plain_size:
        size = len(payload).to_bytes(4, "big")
    else:
        size = synchsafe(len(payload))
    return ident + size + flags.to_bytes(2, "big") + payload


def text(frame_id: str, *values: str, encoding: int = UTF8, version: int = 4) -> bytes:
    sep = b"\x00\x00" if encoding in (UTF16, UTF16BE) else b"\x00"
    return frame(frame_id, bytes([encoding]) + sep.join(encode(v, encoding) for v in values),
                 version)


def txxx(desc: str, value: str, encoding: int = UTF8, version: int = 4) -> bytes:
    """A user text frame; the value ends with a terminator, as many taggers write it."""
    return frame("TXX" if version == 2 else "TXXX",
                 bytes([encoding]) + encode(desc, encoding, True)
                 + encode(value, encoding, True), version)


def url(frame_id: str, address: str, version: int = 4) -> bytes:
    return frame(frame_id, address.encode("latin-1"), version)


def wxxx(desc: str, address: str, encoding: int = UTF8, version: int = 4) -> bytes:
    return frame("WXXX", bytes([encoding]) + encode(desc, encoding, True)
                 + address.encode("latin-1"), version)


def comm(textvalue: str, desc: str = "", lang: str = "eng", encoding: int = UTF8,
         version: int = 4, frame_id: str = "COMM") -> bytes:
    if version == 2 and frame_id == "COMM":
        frame_id = "COM"
    return frame(frame_id, bytes([encoding]) + lang.encode("latin-1")
                 + encode(desc, encoding, True) + encode(textvalue, encoding), version)


def uslt(lyrics: str, desc: str = "", lang: str = "eng", encoding: int = UTF8,
         version: int = 4) -> bytes:
    return comm(lyrics, desc, lang, encoding, version, frame_id="USLT")


def sylt(lines: list[tuple[str, int]], desc: str = "", lang: str = "eng",
         encoding: int = UTF8, version: int = 4) -> bytes:
    body = b"".join(encode(t, encoding, True) + ms.to_bytes(4, "big") for t, ms in lines)
    return frame("SYLT", bytes([encoding]) + lang.encode("latin-1") + b"\x02\x01"
                 + encode(desc, encoding, True) + body, version)


def apic(image: bytes, mime: str = "image/jpeg", picture_type: int = 3, desc: str = "",
         encoding: int = UTF8, version: int = 4) -> bytes:
    if version == 2:
        fmt = {"image/jpeg": b"JPG", "image/png": b"PNG"}.get(mime, b"JPG")
        return frame("PIC", bytes([encoding]) + fmt + bytes([picture_type])
                     + encode(desc, encoding, True) + image, version)
    return frame("APIC", bytes([encoding]) + mime.encode("latin-1") + b"\x00"
                 + bytes([picture_type]) + encode(desc, encoding, True) + image, version)


def geob(data: bytes, filename: str = "notes.txt", mime: str = "text/plain",
         desc: str = "notes", encoding: int = UTF8, version: int = 4) -> bytes:
    return frame("GEOB", bytes([encoding]) + mime.encode("latin-1") + b"\x00"
                 + encode(filename, encoding, True) + encode(desc, encoding, True) + data,
                 version)


def priv(owner: str, data: bytes, version: int = 4) -> bytes:
    return frame("PRIV", owner.encode("latin-1") + b"\x00" + data, version)


def ufid(owner: str, identifier: bytes, version: int = 4) -> bytes:
    return frame("UFI" if version == 2 else "UFID", owner.encode("latin-1") + b"\x00"
                 + identifier, version)


def popm(email: str, rating: int = 196, count: int = 42, version: int = 4) -> bytes:
    return frame("POP" if version == 2 else "POPM", email.encode("latin-1") + b"\x00"
                 + bytes([rating]) + count.to_bytes(4, "big"), version)


def owne(price: str = "EUR0.99", date: str = "20240501", seller: str = "Example Music Store",
         encoding: int = UTF8, version: int = 4) -> bytes:
    return frame("OWNE", bytes([encoding]) + price.encode("latin-1") + b"\x00"
                 + date.encode("latin-1") + encode(seller, encoding), version)


def pcnt(count: int, version: int = 4) -> bytes:
    return frame("PCNT", count.to_bytes(4, "big"), version)


def chap(element: str, start_ms: int, end_ms: int, *subframes: bytes, version: int = 4) -> bytes:
    return frame("CHAP", element.encode("latin-1") + b"\x00" + start_ms.to_bytes(4, "big")
                 + end_ms.to_bytes(4, "big") + b"\xff" * 8 + b"".join(subframes), version)


def ctoc(element: str, children: list[str], *subframes: bytes, version: int = 4) -> bytes:
    return frame("CTOC", element.encode("latin-1") + b"\x00" + b"\x03"
                 + bytes([len(children)]) + b"".join(c.encode("latin-1") + b"\x00"
                                                     for c in children)
                 + b"".join(subframes), version)


def compressed_frame(frame_id: str, payload: bytes, version: int = 4) -> bytes:
    """A zlib-compressed frame (2.3: flag 0x0080 and the size before
    compression; 2.4: flags 0x0008 compression + 0x0001 data length)."""
    packed = zlib.compress(payload)
    if version == 3:
        return frame(frame_id, len(payload).to_bytes(4, "big") + packed, 3, flags=0x0080)
    return frame(frame_id, synchsafe(len(payload)) + packed, 4, flags=0x0009)


def encrypted_frame(frame_id: str, payload: bytes, method: int = 0x80,
                    version: int = 4) -> bytes:
    if version == 3:
        return frame(frame_id, bytes([method]) + payload, 3, flags=0x0040)
    return frame(frame_id, bytes([method]) + payload, 4, flags=0x0004)


def tag(*frames: bytes, version: int = 4, padding: int = 0, padding_bytes: bytes = b"",
        flags: int = 0, extended: bytes | None = None, footer: bool = False,
        unsynchronise: bool = False, revision: int = 0) -> bytes:
    """A complete tag. ``padding_bytes`` replace the start of the padding.
    With ``unsynchronise`` the whole tag body (2.2, 2.3) or every frame's
    data (2.4: set the frames' unsync flag yourself, or use the tag flag
    alone, which applies to all frames) is unsynchronised."""
    body = b"".join(frames)
    if extended is not None:
        body = extended + body
        flags |= 0x40
    body += padding_bytes + bytes(max(0, padding - len(padding_bytes)))
    if unsynchronise:
        flags |= 0x80
        if version < 4:
            body = unsync(body)
    if footer:
        flags |= 0x10
    head = b"ID3" + bytes([version, revision, flags]) + synchsafe(len(body))
    out = head + body
    if footer:
        out += b"3DI" + head[3:]
    return out


def unsync_frame_v24(frame_id: str, payload: bytes) -> bytes:
    """A 2.4 frame with its own unsynchronisation flag (0x0002)."""
    return frame(frame_id, unsync(payload), 4, flags=0x0002)


def extended_v3(padding_size: int = 0) -> bytes:
    return (6).to_bytes(4, "big") + b"\x00\x00" + padding_size.to_bytes(4, "big")


def extended_v4() -> bytes:
    return synchsafe(6) + b"\x01\x00"


def kitchen_sink_frames(cover: bytes, version: int = 3) -> list[bytes]:
    """Every kind of frame metaex reads, with made-up values."""
    return [
        text("TIT2", "Floor plan blues", version=version),
        text("TPE1", "Jane Doe", version=version),
        text("TALB", "Demo tapes", version=version),
        text("TCOM", "Jane Doe", version=version),
        text("TENC", "Jane Doe", version=version),
        text("TSSE", "LAME 3.100 -V2", version=version),
        text("TCOP", "2024 Jane Doe", version=version),
        text("TYER" if version == 3 else "TDRC", "2024", version=version),
        txxx("Contact", "jane.doe@example.com", version=version),
        txxx("Recorded at", "Home studio, 12 Example Street", encoding=UTF16, version=version),
        wxxx("Homepage", "https://example.com/jane", version=version),
        url("WOAR", "https://example.com/jane/music", version=version),
        comm("Recorded on my phone, first take", version=version),
        uslt("La la la, the kitchen is small", version=version),
        sylt([("La la", 1000), ("the kitchen", 2000)], version=version),
        apic(cover, desc="Cover", version=version),
        geob(b"Ideas for the second verse", version=version),
        priv("WM/UniqueFileIdentifier", b"AMGa_id=R 123456;AMGp_id=P 7890", version=version),
        ufid("http://musicbrainz.org", b"0f9b6c4e-0000-1111-2222-333333333333",
             version=version),
        popm("jane.doe@example.com", version=version),
        owne(version=version),
        pcnt(7, version=version),
    ]
