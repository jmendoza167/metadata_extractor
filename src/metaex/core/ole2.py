"""Just enough of the OLE2 / Compound File format to tell what a file is.

Legacy Office files (.doc, .xls, .ppt) and password-protected Office
Open XML files (an encrypted .docx is not a ZIP but an OLE2 file holding
"EncryptionInfo" and "EncryptedPackage" streams) share the same
container. metaex supports neither; it only reads the names of the
streams in the directory, to say clearly why a file is refused.

The directory is reached by following sector chains through the FAT.
Every chain is bounded by the number of sectors in the file and checked
for loops, so a hostile file can't make this hang.
"""

from __future__ import annotations

from metaex.core.errors import MalformedFileError
from metaex.core.reader import ByteReader

SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
HEADER_SIZE = 512
DIR_ENTRY_SIZE = 128
MAX_DIR_ENTRIES = 100_000
END_OF_CHAIN = 0xFFFFFFFE
FREE_SECT = 0xFFFFFFFF
HEADER_DIFAT = 109

ENCRYPTED_OOXML = "encrypted_ooxml"
WORD = "word"
EXCEL = "excel"
POWERPOINT = "powerpoint"
OUTLOOK = "outlook"
OTHER = "other"


def stream_names(data: bytes | bytearray | memoryview) -> list[str]:
    """Names of all directory entries (streams and storages), in directory
    order. Raises MalformedFileError if the file is not a readable OLE2 file."""
    data = bytes(data)
    if not data.startswith(SIGNATURE):
        raise MalformedFileError("not an OLE2 compound file")
    r = ByteReader(data, "little")
    header = r.sub(0, HEADER_SIZE, "OLE2 header")
    header.seek(0x1C)
    if header.u16("byte order") != 0xFFFE:
        raise MalformedFileError("OLE2 header has a wrong byte order mark")
    shift = header.u16("sector shift")
    if shift not in (9, 12):
        raise MalformedFileError(f"OLE2 sector shift {shift} is not supported")
    sector = 1 << shift
    header.seek(0x2C)
    fat_sectors = header.u32("FAT sector count")
    first_dir = header.u32("first directory sector")
    header.seek(0x44)
    first_difat = header.u32("first DIFAT sector")
    difat_count = header.u32("DIFAT sector count")
    total_sectors = max(0, (len(data) - sector) // sector)  # the header occupies sector -1

    def sector_reader(n: int) -> ByteReader:
        if n >= total_sectors:
            raise MalformedFileError(f"OLE2 sector {n} is outside the file")
        return r.sub(sector + n * sector, sector, f"OLE2 sector {n}")

    # FAT sector numbers: 109 in the header, the rest in a chain of DIFAT sectors.
    if fat_sectors > total_sectors:
        raise MalformedFileError("OLE2 header claims more FAT sectors than the file has")
    header.seek(0x4C)
    fat_list = [header.u32("DIFAT entry") for _ in range(HEADER_DIFAT)]
    next_difat, seen = first_difat, set()
    for _ in range(min(difat_count, total_sectors)):
        if next_difat in (END_OF_CHAIN, FREE_SECT) or next_difat in seen:
            break
        seen.add(next_difat)
        s = sector_reader(next_difat)
        per = sector // 4 - 1
        fat_list += [s.u32("DIFAT entry") for _ in range(per)]
        next_difat = s.u32("next DIFAT sector")
    fat_list = [n for n in fat_list[:fat_sectors] if n not in (FREE_SECT, END_OF_CHAIN)]

    fat: list[int] = []
    for n in fat_list:
        s = sector_reader(n)
        fat += [s.u32("FAT entry") for _ in range(sector // 4)]

    names: list[str] = []
    current, visited = first_dir, set()
    while current != END_OF_CHAIN:
        if current in visited or current >= len(fat) or len(visited) > total_sectors:
            raise MalformedFileError("OLE2 directory chain is broken or loops")
        visited.add(current)
        s = sector_reader(current)
        for _ in range(sector // DIR_ENTRY_SIZE):
            entry = s.sub(s.tell(), DIR_ENTRY_SIZE, "OLE2 directory entry")
            s.skip(DIR_ENTRY_SIZE)
            raw = entry.read(64, "entry name")
            length = entry.u16("entry name length")
            kind = entry.u8("entry type")
            if kind == 0 or length < 2 or length > 64:
                continue  # unused entry
            names.append(raw[:length - 2].decode("utf-16-le", errors="replace"))
            if len(names) > MAX_DIR_ENTRIES:
                raise MalformedFileError("OLE2 directory has too many entries")
        current = fat[current]
    return names


def classify(data: bytes | bytearray | memoryview) -> str:
    """What kind of OLE2 file this is (one of the constants above); OTHER if
    its directory can't be read."""
    try:
        names = set(stream_names(data))
    except MalformedFileError:
        return OTHER
    if {"EncryptionInfo", "EncryptedPackage"} <= names:
        return ENCRYPTED_OOXML
    if "WordDocument" in names:
        return WORD
    if "Workbook" in names or "Book" in names:
        return EXCEL
    if "PowerPoint Document" in names:
        return POWERPOINT
    if any(n.startswith("__substg1.0_") for n in names):
        return OUTLOOK
    return OTHER
