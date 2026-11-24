"""Build macOS AppleDouble files ("._name") and binary property lists, the
way macOS writes them when it puts a file in a ZIP or TAR archive.

All values are fixed, so the output is the same on every machine.
"""

from __future__ import annotations

import struct

QUARANTINE = "com.apple.quarantine"
WHERE_FROMS = "com.apple.metadata:kMDItemWhereFroms"

SAMPLE_QUARANTINE = b"0083;66320a80;Safari;3E2F1A44-5B6C-4D7E-8F90-A1B2C3D4E5F6"
"""Downloaded with Safari on 2024-05-01T09:20:00Z."""
SAMPLE_WHERE_FROMS = ["https://downloads.example.com/plans/floor-plan.zip",
                      "https://www.example.com/jane.doe/shared"]


def bplist(value) -> bytes:
    """A binary property list ("bplist00") of strings, ints and lists of them."""
    objects: list[bytes] = []

    def add(v) -> int:
        index = len(objects)
        objects.append(b"")
        if isinstance(v, str):
            raw = v.encode("ascii")
            objects[index] = _marker(0x5, len(raw)) + raw
        elif isinstance(v, bool):
            objects[index] = bytes([0x09 if v else 0x08])
        elif isinstance(v, int):
            objects[index] = b"\x13" + struct.pack(">q", v)
        elif isinstance(v, list):
            refs = [add(item) for item in v]
            objects[index] = _marker(0xA, len(refs)) + bytes(refs)
        else:
            raise TypeError(type(v))
        return index

    add(value)
    out = bytearray(b"bplist00")
    offsets = []
    for obj in objects:
        offsets.append(len(out))
        out += obj
    table = len(out)
    out += b"".join(struct.pack(">H", o) for o in offsets)
    out += bytes(6) + struct.pack(">BBQQQ", 2, 1, len(objects), 0, table)
    return bytes(out)


def _marker(kind: int, size: int) -> bytes:
    if size < 15:
        return bytes([kind << 4 | size])
    return bytes([kind << 4 | 0xF, 0x10]) + bytes([size]) if size < 256 else \
        bytes([kind << 4 | 0xF, 0x11]) + struct.pack(">H", size)


def appledouble(xattrs: list[tuple[str, bytes]] = (), finder_info: bytes = bytes(32),
                resource_fork: bytes = b"") -> bytes:
    """An AppleDouble file as macOS's copyfile() writes it: a Finder info entry
    holding an "ATTR" block with the extended attributes, and a resource fork
    entry."""
    names = [(n.encode("utf-8") + b"\x00", v) for n, v in xattrs]
    header_size = 50 + 32 + 2  # AppleDouble header + Finder info + pad
    entries = bytearray()
    pos = header_size + 36
    layout = []
    for raw_name, value in names:
        entry_len = 11 + len(raw_name)
        layout.append((pos, raw_name, value))
        pos = (pos + entry_len + 3) & ~3
    data_start = pos
    offsets = []
    for _pos, _raw, value in layout:
        offsets.append(pos)
        pos += len(value)
    attr_end = pos
    for (entry_pos, raw_name, value), offset in zip(layout, offsets):
        record = struct.pack(">IIHB", offset, len(value), 0, len(raw_name)) + raw_name
        entries += record + bytes(((entry_pos + len(record) + 3) & ~3) - entry_pos - len(record))
    attr_header = (b"ATTR" + struct.pack(">IIII", 0, attr_end, data_start, attr_end - data_start)
                   + bytes(12) + struct.pack(">HH", 0, len(names)))
    finder_entry_len = attr_end - 50
    rsrc_offset = attr_end
    head = (struct.pack(">II", 0x00051607, 0x00020000) + b"Mac OS X        "
            + struct.pack(">H", 2)
            + struct.pack(">III", 9, 50, finder_entry_len)
            + struct.pack(">III", 2, rsrc_offset, len(resource_fork)))
    out = head + finder_info + b"\x00\x00" + attr_header + bytes(entries)
    assert len(out) == data_start, (len(out), data_start)
    out += b"".join(v for _p, _r, v in layout)
    return out + resource_fork


def sample_appledouble() -> bytes:
    """The AppleDouble file of a downloaded file: quarantine and where-from URLs."""
    return appledouble([(QUARANTINE, SAMPLE_QUARANTINE),
                        (WHERE_FROMS, bplist(SAMPLE_WHERE_FROMS))])
