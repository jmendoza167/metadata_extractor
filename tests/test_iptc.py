from helpers.jpeg import iim, photoshop_resource
from metaex.core.iptc import iptc_section, parse_iim, parse_image_resources, resources_section

DATASETS = iim(
    (1, 90, b"\x1b%G"),
    (2, 0, b"\x00\x04"),
    (2, 80, "Jane Doe"),
    (2, 90, "Paris"),
    (2, 101, "France"),
    (2, 120, "Caption with caf" + chr(0xE9)),
    (2, 25, "holiday"),
    (2, 25, "beach"),
    (2, 116, "(c) Jane"),
    (2, 250, "mystery"),
)


def test_parse_iim_and_section():
    warnings = []
    datasets = parse_iim(DATASETS, warnings)
    assert warnings == []
    f = {x.label: x for x in iptc_section(datasets).fields}
    assert f["CodedCharacterSet"].value == "UTF-8"
    assert f["RecordVersion"].value == "4"
    assert f["By-line"].value == "Jane Doe"
    assert f["City"].value == "Paris"
    assert f["Country-PrimaryLocationName"].value == "France"
    assert f["Caption-Abstract"].value == "Caption with caf" + chr(0xE9)
    assert f["Keywords"].value == "holiday; beach"
    assert f["CopyrightNotice"].value == "(c) Jane"
    assert f["Unknown dataset 2:250"].value == "mystery"
    assert f["By-line"].sensitive and f["City"].sensitive
    assert not f["RecordVersion"].sensitive


def test_latin1_fallback_without_utf8_marker():
    datasets = parse_iim(iim((2, 90, b"Z\xfcrich")), [])
    f = {x.label: x for x in iptc_section(datasets).fields}
    assert f["City"].value == "Z" + chr(0xFC) + "rich"


def test_image_resources():
    data = (photoshop_resource(0x03ED, bytes(16)) + photoshop_resource(0x0404, DATASETS)
            + photoshop_resource(0x040C, bytes(31), name=b"thumb"))
    warnings = []
    resources = parse_image_resources(data, warnings)
    assert [r.id for r in resources] == [0x03ED, 0x0404, 0x040C]
    assert resources[2].name == "thumb"
    section = resources_section(resources, warnings)
    labels = [f.label for f in section.fields]
    assert labels == ["0x03ED Resolution info", "0x0404 IPTC-NAA record",
                      "0x040C Photoshop thumbnail"]
    assert any("Photoshop thumbnail present" in w for w in warnings)


def test_truncated_resources_keep_earlier_ones():
    data = photoshop_resource(0x03ED, bytes(16)) + photoshop_resource(0x0404, DATASETS)[:20]
    warnings = []
    assert [r.id for r in parse_image_resources(data, warnings)] == [0x03ED]
    assert "truncated" in warnings[0]


def test_truncated_iim():
    warnings = []
    assert len(parse_iim(DATASETS[:-3], warnings)) == 9
    assert "truncated" in warnings[0]
