"""Build minimal OLE2 compound files: a header, one FAT sector and a
directory listing the given stream names (the streams are empty).

Enough to look like a legacy .doc/.xls/.ppt or a password-protected
Office document to anything that reads the directory.
"""

from __future__ import annotations

import struct

SECTOR = 512
FREE, END, FATSECT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD


def _dir_entry(name: str, kind: int) -> bytes:
    raw = name.encode("utf-16-le") + b"\x00\x00"
    return (raw.ljust(64, b"\x00") + struct.pack("<HBB", len(raw), kind, 1)
            + struct.pack("<III", FREE, FREE, FREE) + b"\x00" * 16 + b"\x00" * 4
            + b"\x00" * 16 + struct.pack("<IQ", END, 0))


def ole2_file(*streams: str, loop: bool = False, dir_sector: int = 1) -> bytes:
    """An OLE2 file whose directory holds a root entry and ``streams``.

    ``loop`` makes the directory chain point back to itself (a hostile
    file); ``dir_sector`` can point the header at a sector that doesn't exist.
    """
    entries = [_dir_entry("Root Entry", 5)] + [_dir_entry(s, 2) for s in streams]
    per_sector = SECTOR // 128
    while len(entries) % per_sector:
        entries.append(b"\x00" * 128)
    dir_sectors = [b"".join(entries[i:i + per_sector]) for i in range(0, len(entries),
                                                                      per_sector)]
    fat = [FATSECT] + [i + 2 for i in range(len(dir_sectors) - 1)] + [END]
    if loop:
        fat[-1] = 1
    fat += [FREE] * (SECTOR // 4 - len(fat))
    header = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 16
              + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\x00" * 6
              + struct.pack("<IIIIIIIII", 0, 1, dir_sector, 0, 4096, END, 0, END, 0)
              + struct.pack("<I", 0) + struct.pack("<I", FREE) * 108)
    assert len(header) == SECTOR
    return header + struct.pack(f"<{SECTOR // 4}I", *fat) + b"".join(dir_sectors)


def encrypted_office() -> bytes:
    """What Office saves for a password-protected .docx/.xlsx/.pptx."""
    return ole2_file("EncryptionInfo", "EncryptedPackage", "\x06DataSpaces")


def legacy_word() -> bytes:
    return ole2_file("WordDocument", "1Table", "\x05SummaryInformation")


def legacy_excel() -> bytes:
    return ole2_file("Workbook", "\x05SummaryInformation")


def legacy_powerpoint() -> bytes:
    return ole2_file("PowerPoint Document", "Current User")
