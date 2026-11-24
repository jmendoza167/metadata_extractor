import hashlib

import pytest

from helpers.jpeg import (
    app, corrupt_scan, icc_profile, icc_segments, kitchen_sink_jpeg, make_jpeg, sample_photo,
    segment,
)
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
    with pytest.raises(UnsupportedFormatError, match="can't verify ZIP"):
        verify(b"PK\x05\x06" + bytes(18))  # an empty ZIP


def test_image_data_match_is_none_without_original():
    result = verify(jpeg.strip(sample_photo()))
    assert result.image_data_match is None
    assert len(result.image_sha256) == 64
    assert result.original_image_sha256 is None


# --- JPEG-specific checks ----------------------------------------------------------


def test_image_data_matches_original():
    original = kitchen_sink_jpeg()
    result = verify(jpeg.strip(original), original)
    assert result.clean
    assert result.image_data_match is True
    assert result.image_sha256 == result.original_image_sha256
    assert result.image_sha256 == hashlib.sha256(jpeg.image_data(original)).hexdigest()


def test_changed_image_data_is_detected():
    original = sample_photo()
    cleaned = jpeg.strip(original)
    altered = corrupt_scan(cleaned)
    assert altered != cleaned
    result = verify(altered, original)
    assert result.image_data_match is False
    assert not result.clean
    assert "image data differs from the original" in result.problems


def test_exif_with_more_than_orientation_is_a_leftover():
    from helpers.tiff import ASCII, SHORT, build_tiff, exif_payload

    data = make_jpeg(app(1, exif_payload(build_tiff([(0x0112, SHORT, 6), (0x010F, ASCII, "C")]))))
    result = verify(data)
    assert not result.clean
    assert ("EXIF", "Make") in labels(result.leftovers)
    assert any(f.section == "JPEG structure" and "EXIF contains more" in f.value
               for f in result.leftovers)


def test_orientation_exif_with_hidden_bytes_is_a_leftover():
    minimal = jpeg.minimal_orientation_exif(6)
    payload = minimal[4:] + b"hidden data here"
    result = verify(make_jpeg(app(1, payload)))
    assert ("EXIF", "Orientation") in labels(result.kept_by_design)
    assert not result.clean
    assert any("unused bytes" in f.value for f in result.leftovers)


@pytest.mark.parametrize(
    "segment_bytes,text",
    [
        (app(0, b"JFIF\x00\x01\x02\x01\x00\x48\x00\x48\x00\x00" + b"secret"), "extra bytes in JFIF"),
        (app(14, b"Adobe\x00\x64\x00\x00\x00\x00\x01" + b"secret"), "extra bytes in Adobe"),
        (app(2, b"ICC_PROFILE\x00\x02\x02" + bytes(10)), "ICC profile chunk"),
        (segment(0xFE, b"note"), "COM segment is not allowed"),
        (app(1, b"http://ns.adobe.com/xmp/extension/\x00short"), "APP1"),
    ],
)
def test_hidden_or_disallowed_segments_are_leftovers(segment_bytes, text):
    result = verify(make_jpeg(segment_bytes))
    assert not result.clean
    assert any(text in f.value for f in result.leftovers if f.section == "JPEG structure")


def test_nothing_after_eoi():
    result = verify(jpeg.strip(sample_photo()) + b"more")
    assert not result.clean
    assert any(f.section == "Trailing data" for f in result.leftovers)


def test_original_that_is_not_a_jpeg():
    result = verify(jpeg.strip(sample_photo()), b"not a jpeg")
    assert result.image_data_match is None
    assert any("original is not a complete JPEG" in p for p in result.problems)
