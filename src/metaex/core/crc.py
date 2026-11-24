"""Small CRCs used by audio formats, table-driven, in pure Python.

- crc8: polynomial 0x07, initial value 0 (FLAC frame headers).
- crc16: polynomial 0x8005, most significant bit first, initial value 0
  (FLAC frames; also known as CRC-16/UMTS or CRC-16/BUYPASS).
- crc16_arc: polynomial 0x8005 reflected (0xA001), initial value 0
  (the LAME tag of MP3 files; also known as CRC-16/ARC).
"""

from __future__ import annotations


def _table_msb(poly: int, width: int) -> list[int]:
    top, mask = 1 << (width - 1), (1 << width) - 1
    table = []
    for byte in range(256):
        crc = byte << (width - 8)
        for _ in range(8):
            crc = ((crc << 1) ^ poly) if crc & top else (crc << 1)
        table.append(crc & mask)
    return table


def _table_reflected(poly: int) -> list[int]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
        table.append(crc)
    return table


_CRC8 = _table_msb(0x07, 8)
_CRC16 = _table_msb(0x8005, 16)
_CRC16_ARC = _table_reflected(0xA001)


def crc8(data: bytes | bytearray | memoryview, crc: int = 0) -> int:
    table = _CRC8
    for b in bytes(data):
        crc = table[crc ^ b]
    return crc


def crc16(data: bytes | bytearray | memoryview, crc: int = 0) -> int:
    table = _CRC16
    for b in bytes(data):
        crc = ((crc << 8) & 0xFFFF) ^ table[(crc >> 8) ^ b]
    return crc


def crc16_arc(data: bytes | bytearray | memoryview, crc: int = 0) -> int:
    table = _CRC16_ARC
    for b in bytes(data):
        crc = (crc >> 8) ^ table[(crc ^ b) & 0xFF]
    return crc
