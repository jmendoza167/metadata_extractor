import pytest

from helpers.jpeg import kitchen_sink_jpeg
from metaex.core.result import Section
from metaex.core.risk import CRITICAL, HIGH, LOW, MEDIUM, assess, classify, counts
from metaex.formats import jpeg


@pytest.mark.parametrize(
    "section,key,label,level",
    [
        # critical
        ("gps", "coordinates", "Coordinates", CRITICAL),
        ("gps", "GPSVersionID", "GPSVersionID", CRITICAL),
        ("xmp", "exif:GPSLatitude", "exif:GPSLatitude", CRITICAL),
        ("xmp", "(extended) exif:GPSLongitude", "(extended) exif:GPSLongitude", CRITICAL),
        ("thumbnail", "embedded_thumbnail", "Embedded thumbnail", CRITICAL),
        ("jfif", "thumbnail", "JFIF thumbnail", CRITICAL),
        ("photoshop", "0x040c", "0x040C Photoshop thumbnail", CRITICAL),
        ("mpf", "image_2", "Image 2", CRITICAL),
        ("trailing", "size", "Size", CRITICAL),
        ("exif", "BodySerialNumber", "BodySerialNumber", CRITICAL),
        ("exif", "CameraOwnerName", "CameraOwnerName", CRITICAL),
        ("xmp", "aux:SerialNumber", "aux:SerialNumber", CRITICAL),
        # high
        ("exif", "Make", "Make", HIGH),
        ("exif", "Model", "Model", HIGH),
        ("exif", "DateTimeOriginal", "DateTimeOriginal", HIGH),
        ("exif", "OffsetTimeOriginal", "OffsetTimeOriginal", HIGH),
        ("exif", "Artist", "Artist", HIGH),
        ("exif", "XPAuthor", "XPAuthor", HIGH),
        ("exif", "Software", "Software", HIGH),
        ("xmp", "xmpMM:History[1]/stEvt:action", "xmpMM:History[1]/stEvt:action", HIGH),
        ("xmp", "xmpMM:DocumentID", "xmpMM:DocumentID", HIGH),
        ("xmp", "xmp:CreateDate", "xmp:CreateDate", HIGH),
        ("xmp", "dc:creator", "dc:creator", HIGH),
        ("mark_of_the_web", "host_url", "Host URL", HIGH),
        ("comments", "comment_1", "Comment 1", HIGH),
        ("iptc", "2:80", "By-line", HIGH),
        ("iptc", "2:90", "City", HIGH),
        ("iptc", "2:55", "DateCreated", HIGH),
        # medium
        ("exif", "ExposureTime", "ExposureTime", MEDIUM),
        ("exif", "FNumber", "FNumber", MEDIUM),
        ("exif", "ISOSpeedRatings", "ISOSpeedRatings", MEDIUM),
        ("exif", "LensModel", "LensModel", MEDIUM),
        ("xmp", "exif:FNumber", "exif:FNumber", MEDIUM),
        ("icc", "device_manufacturer", "Device manufacturer", MEDIUM),
        ("icc", "device_model", "Device model", MEDIUM),
        ("other_app", "APP12@2", 'APP12 "Ducky"', MEDIUM),
        # low
        ("exif", "Orientation", "Orientation", LOW),
        ("exif", "byte_order", "Byte order", LOW),
        ("icc", "description", "Description", LOW),
        ("icc", "copyright", "Copyright", LOW),
        ("icc", "created", "Profile created", LOW),
        ("jfif", "density", "Density", LOW),
        ("adobe", "transform", "Color transform", LOW),
        ("exif", "tag_0xbeef", "Unknown tag 0xBEEF", LOW),
    ],
)
def test_classify(section, key, label, level):
    assert classify(section, key, label) == level


def test_assess_kitchen_sink():
    findings = assess(jpeg.parse(kitchen_sink_jpeg())["sections"])
    levels = [f.level for f in findings]
    assert levels == sorted(levels, key=[CRITICAL, HIGH, MEDIUM, LOW].index)
    c = counts(findings)
    assert c[CRITICAL] > 0 and c[HIGH] > 0 and c[MEDIUM] > 0 and c[LOW] > 0
    critical_sections = {f.section for f in findings if f.level == CRITICAL}
    assert {"GPS", "Thumbnail", "MPF (multi-picture)", "Trailing data", "XMP"} <= critical_sections


def test_non_metadata_sections_are_ignored():
    sections = [Section("file_system", "File system"), Section("hashes", "Hashes"),
                Section("file_type", "File type"), Section("structure", "JPEG structure")]
    for s in sections:
        s.add("x", "Make", "value")
    assert assess(sections) == []


def test_stripped_file_has_only_low_findings():
    findings = assess(jpeg.parse(jpeg.strip(kitchen_sink_jpeg()))["sections"])
    assert {f.level for f in findings} <= {LOW, MEDIUM}
    assert all(f.section == "ICC profile" for f in findings if f.level == MEDIUM)
