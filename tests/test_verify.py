import pytest

from helpers.jpeg import app, icc_profile, icc_segments, kitchen_sink_jpeg, make_jpeg, sample_photo
from metaex.core.errors import UnsupportedFormatError
from metaex.core.verify import verify
from metaex.formats import jpeg


def labels(findings):
    return [(f.section, f.label) for f in findings]


def test_stripped_file_is_clean():
    result = verify(jpeg.strip(kitchen_sink_jpeg()))
    assert result.clean
    assert result.leftovers == []
    assert result.problems == []
    kept_sections = {f.section for f in result.kept_by_design}
    assert kept_sections == {"JFIF", "ICC profile", "Adobe"}
    assert result.filetype.key == "jpeg"


def test_orientation_is_kept_by_design():
    from helpers.tiff import SHORT, build_tiff, exif_payload

    data = make_jpeg(app(1, exif_payload(build_tiff([(0x0112, SHORT, 6)]))))
    result = verify(jpeg.strip(data))
    assert result.clean
    assert ("EXIF", "Orientation") in labels(result.kept_by_design)


def test_unstripped_file_has_leftovers():
    result = verify(sample_photo())
    assert not result.clean
    found = labels(result.leftovers)
    assert ("GPS", "Coordinates") in found
    assert ("EXIF", "Make") in found
    assert ("Thumbnail", "Embedded thumbnail") in found
    assert any(section == "XMP" for section, _ in found)


def test_any_section_outside_the_whitelist_is_a_leftover():
    result = verify(make_jpeg(app(12, b"Ducky\x00\x01")))
    assert not result.clean
    assert labels(result.leftovers)[0][0] == "Other APP segments"


def test_extra_fields_in_a_kept_section_are_leftovers():
    jfif_with_thumb = b"JFIF\x00\x01\x02\x01\x00\x48\x00\x48\x02\x02" + bytes(12)
    result = verify(make_jpeg(app(0, jfif_with_thumb)))
    assert ("JFIF", "JFIF thumbnail") in labels(result.leftovers)
    assert ("JFIF", "Density") in labels(result.kept_by_design)


def test_malformed_file_is_not_clean():
    data = jpeg.strip(make_jpeg(*icc_segments(icc_profile())))
    result = verify(data[:-60])
    assert not result.clean
    assert "malformed" in result.problems[0]


def test_unsupported_type_raises():
    with pytest.raises(UnsupportedFormatError, match="can't verify PNG"):
        verify(b"\x89PNG\r\n\x1a\n" + bytes(20))


def test_image_data_match_is_none_without_original():
    assert verify(jpeg.strip(sample_photo())).image_data_match is None
