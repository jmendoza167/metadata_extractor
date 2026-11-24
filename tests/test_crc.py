"""The CRCs of core/crc.py, against the standard check values ("123456789")."""

from metaex.core.crc import crc8, crc16, crc16_arc

CHECK = b"123456789"


def test_check_values():
    assert crc8(CHECK) == 0xF4  # CRC-8 (poly 0x07)
    assert crc16(CHECK) == 0xFEE8  # CRC-16/UMTS (poly 0x8005, MSB first)
    assert crc16_arc(CHECK) == 0xBB3D  # CRC-16/ARC


def test_incremental_and_empty():
    assert crc8(b"") == 0 and crc16(b"") == 0 and crc16_arc(b"") == 0
    assert crc16(CHECK[4:], crc16(CHECK[:4])) == crc16(CHECK)
    assert crc8(CHECK[4:], crc8(CHECK[:4])) == crc8(CHECK)
    assert crc16_arc(CHECK[4:], crc16_arc(CHECK[:4])) == crc16_arc(CHECK)
    assert crc16(memoryview(CHECK)) == crc16(bytearray(CHECK)) == 0xFEE8
