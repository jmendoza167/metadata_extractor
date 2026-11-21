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


def _xmp(*fields):
    section = Section("xmp", "XMP")
    for key, value in fields:
        section.add(key, key, value)
    return section


def test_standard_and_extended_xmp_are_counted_once():
    section = _xmp(("dc:creator", "Jane Doe"), ("xmp:CreatorTool", "Editor 2.0"),
                   ("(extended) dc:creator", "Jane Doe"),
                   ("(extended) xmp:CreatorTool", "Editor 2.0"),
                   ("(extended) exif:GPSLatitude", "48,51.502N"))
    findings = assess([section])
    assert [f.label for f in findings] == ["(extended) exif:GPSLatitude", "dc:creator",
                                           "xmp:CreatorTool"]
    assert counts(findings) == {CRITICAL: 1, HIGH: 2, MEDIUM: 0, LOW: 0}


def test_merged_xmp_keeps_the_highest_level(monkeypatch):
    from metaex.core import risk

    monkeypatch.setattr(risk, "classify",
                        lambda section, key, label="": HIGH if key.startswith("(ext") else LOW)
    findings = assess([_xmp(("xmp:Rating", "5"), ("(extended) xmp:Rating", "5"))])
    assert [(f.level, f.field_key) for f in findings] == [(HIGH, "(extended) xmp:Rating")]


def test_different_values_are_not_merged():
    findings = assess([_xmp(("dc:creator", "Jane Doe"), ("(extended) dc:creator", "John"))])
    assert len(findings) == 2


def test_same_names_outside_xmp_are_not_merged():
    exif = Section("exif", "EXIF")
    exif.add("Make", "Make", "ExampleCam")
    exif.add("Make", "Make", "ExampleCam")
    assert len(assess([exif])) == 2


def test_kitchen_sink_xmp_not_double_counted():
    parsed = jpeg.parse(kitchen_sink_jpeg())
    findings = assess(parsed["sections"])
    xmp = [f for f in findings if f.section_key == "xmp"]
    paths = [f.field_key.replace("(extended) ", "") for f in xmp]
    values = [(p, f.value) for p, f in zip(paths, xmp)]
    assert len(values) == len(set(values))
    assert not any(f.field_key.startswith("(extended) ") for f in xmp)


@pytest.mark.parametrize(
    "section,key,label,level",
    [
        ("gps", "coordinates", "Coordinates", CRITICAL),       # from PNG eXIf
        ("trailing", "size", "Size", CRITICAL),                # data after IEND
        ("text", "Author", "Author", HIGH),
        ("text", "My Key", "My Key", HIGH),                    # unknown keywords too
        ("text", "Creation Time", "Creation Time", HIGH),
        ("ai", "parameters", "parameters", HIGH),
        ("ai", "workflow", "workflow", HIGH),
        ("xmp", "xmpMM:History[1]/stEvt:action", "xmpMM:History[1]/stEvt:action", HIGH),
        ("time", "tIME", "Last modified", HIGH),
        ("content_credentials", "caBX", "C2PA manifest", HIGH),
        ("icc", "profile_name", "Profile name", MEDIUM),
        ("icc", "device_model", "Device model", MEDIUM),
        ("color", "mDCv", "Mastering display", MEDIUM),
        ("unknown", "prVt@50", "prVt", MEDIUM),
        ("color", "sRGB", "sRGB", LOW),
        ("color", "pHYs", "Pixel density", LOW),
        ("animation", "frames", "Frames", LOW),
    ],
)
def test_classify_png_fields(section, key, label, level):
    assert classify(section, key, label) == level


@pytest.mark.parametrize(
    "section,key,label,level",
    [
        ("comments", "comment_1", "Comment", HIGH),
        ("plain_text", "plain_text_1", "Plain text", HIGH),
        ("app_extensions", "MGK8BIM0000@20", "MGK8BIM0000", HIGH),
        ("app_extensions", "Extension 0x42@20", "Extension 0x42", HIGH),
        ("xmp", "exif:GPSLatitude", "exif:GPSLatitude", CRITICAL),
        ("xmp", "xmp:CreatorTool", "xmp:CreatorTool", HIGH),
        ("trailing", "size", "Size", CRITICAL),
        ("padding", "size", "Size", LOW),
        ("animation", "loop_count", "Loop count", LOW),
        ("animation", "frames", "Frames", LOW),
    ],
)
def test_classify_gif_fields(section, key, label, level):
    assert classify(section, key, label) == level


def test_assess_gif():
    from helpers.gif import application, comment, image, loop, make_gif, xmp_extension
    from helpers.jpeg import SAMPLE_XMP
    from metaex.formats import gif

    data = make_gif(comment("Jane"), loop(), xmp_extension(SAMPLE_XMP), image(),
                    application(b"MGK8BIM0000", b"x"), trailing=b"secret")
    found = {(f.section_key, f.level) for f in assess(gif.parse(data)["sections"])}
    assert ("comments", HIGH) in found and ("app_extensions", HIGH) in found
    assert ("xmp", CRITICAL) in found and ("trailing", CRITICAL) in found
    assert ("animation", LOW) in found


def test_webp_findings_match_jpeg_and_png_rules():
    """WebP sections use the same keys as JPEG/PNG, so the same rules apply."""
    from test_show import _webp_with_metadata
    from metaex.formats import webp

    found = {}
    for f in assess(webp.parse(_webp_with_metadata())["sections"]):
        found.setdefault((f.section_key, f.field_key), f.level)
    assert found[("gps", "coordinates")] == CRITICAL
    assert found[("trailing", "size")] == CRITICAL
    assert found[("exif", "Make")] == HIGH
    assert found[("xmp", "xmp:CreatorTool")] == HIGH
    assert found[("icc", "device_manufacturer")] == MEDIUM
    assert [level for (section, _key), level in found.items() if section == "unknown"] == [
        MEDIUM]
    assert found[("animation", "frames")] == LOW
    assert found[("exif", "Orientation")] == LOW
