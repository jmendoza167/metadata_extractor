"""GIF: stripping with a block whitelist."""

import pytest

from helpers.gif import (application, comment, graphic_control, icc_extension, image, loop,
                         lzw_decode, make_gif, pixels, plain_text, sub_blocks, xmp_extension)
from helpers.jpeg import SAMPLE_XMP, icc_profile
from metaex.core.errors import MalformedFileError
from metaex.formats import gif


def metadata_gif(**kwargs):
    return make_gif(comment("Made by Jane"), loop(0), icc_extension(icc_profile()),
                    xmp_extension(SAMPLE_XMP), graphic_control(delay=20), image(),
                    graphic_control(delay=5), plain_text("hello"),
                    graphic_control(delay=30, transparent=2), image(seed=7),
                    application(b"MGK8BIM0000", b"x" * 30), b"\x21\x42" + sub_blocks(b"odd"),
                    comment("bye"), graphic_control(), trailing=b"after the trailer", **kwargs)


def names(data):
    s = gif.walk_blocks(data)
    return [b.app_id or b.name for b in s.blocks]


def test_strip_keeps_only_whitelisted_blocks():
    cleaned = gif.strip(metadata_gif())
    assert names(cleaned) == ["NETSCAPE2.0", "ICCRGBG1012", "Graphic Control extension",
                              "Image", "Graphic Control extension", "Image", "Trailer"]
    s = gif.walk_blocks(cleaned)
    assert s.end_of_image == len(cleaned)  # nothing after the trailer
    assert gif.parse(cleaned)["warnings"] == []


def test_paranoid_drops_the_icc_profile():
    cleaned = gif.strip(metadata_gif(), paranoid=True)
    assert "ICCRGBG1012" not in names(cleaned)
    assert [s.key for s in gif.parse(cleaned)["sections"]] == ["animation"]


def test_image_data_is_copied_byte_for_byte():
    original = metadata_gif()
    cleaned = gif.strip(original)
    o, c = gif.walk_blocks(original), gif.walk_blocks(cleaned)
    assert cleaned[: c.screen_end] == original[: o.screen_end]
    for a, b in zip(o.images, c.images, strict=True):
        assert original[a.offset : a.end] == cleaned[b.offset : b.end]
    assert gif.image_data(cleaned) == gif.image_data(original)
    for img, seed in zip(c.images, (1, 7)):
        assert lzw_decode(b"".join(img.sub_blocks), img.min_code_size) == pixels(4, 4, 4, seed)


def test_graphic_control_follows_its_image():
    """The delay-5 control belongs to the dropped Plain Text, the last one to
    nothing; the images keep theirs (delay 20 and 30 with transparency)."""
    cleaned = gif.strip(metadata_gif())
    gces = [b.sub_blocks[0] for b in gif.walk_blocks(cleaned).extensions(gif.GRAPHIC_CONTROL)]
    assert [int.from_bytes(g[1:3], "little") for g in gces] == [20, 30]
    assert gces[1][0] & 1 and gces[1][3] == 2


def test_reserved_bits_and_extra_sub_blocks_are_not_kept():
    odd_gce = b"\x21\xf9\x04\xe1\x0a\x00\x03" + b"\x05hello\x00"  # reserved bits + extra data
    odd_loop = (b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x02\x00" + b"\x06secret\x00")
    cleaned = gif.strip(make_gif(odd_loop, odd_gce, image()))
    assert b"hello" not in cleaned and b"secret" not in cleaned
    s = gif.walk_blocks(cleaned)
    assert s.extensions(gif.GRAPHIC_CONTROL)[0].sub_blocks == [b"\x01\x0a\x00\x03"]
    assert s.applications()[0].sub_blocks == [b"NETSCAPE2.0", b"\x01\x02\x00"]


def test_loop_extension_variants():
    cleaned = gif.strip(make_gif(loop(3, b"ANIMEXTS1.0"), loop(9), image(), image()))
    apps = gif.walk_blocks(cleaned).applications()
    assert [(a.app_id, gif.loop_count(a)) for a in apps] == [("ANIMEXTS1.0", 3)]
    invalid = b"\x21\xff\x0bNETSCAPE2.0\x02\x09\x09\x00"
    assert gif.walk_blocks(gif.strip(make_gif(invalid, image()))).applications() == []


def test_icc_profile_is_cut_to_its_declared_size():
    profile = icc_profile()
    cleaned = gif.strip(make_gif(icc_extension(profile + b"hidden bytes"), image()))
    [ext] = gif.walk_blocks(cleaned).applications()
    assert gif.icc_profile(ext) == profile


@pytest.mark.parametrize("profile", [b"not a profile", bytes(200)])
def test_broken_icc_profile_is_dropped(profile):
    cleaned = gif.strip(make_gif(icc_extension(profile), image()))
    assert gif.walk_blocks(cleaned).applications() == []


def test_strip_is_idempotent():
    for paranoid in (False, True):
        once = gif.strip(metadata_gif(), paranoid=paranoid)
        assert gif.strip(once, paranoid=paranoid) == once


def test_clean_gif_is_unchanged():
    data = make_gif(graphic_control(), image())
    assert gif.strip(data) == data


@pytest.mark.parametrize("data,message", [
    (make_gif(trailer=False), "missing GIF trailer"),
    (make_gif(comment("only a comment")), "no image found"),
    (make_gif(comment("x"))[:-3] + b"\x3b", "truncated"),
    (make_gif(b"\x21\xf9\x02\x00\x00\x00", image()), "Graphic Control extension"),
    (make_gif(b"\x21\xf9\x00", image()), "Graphic Control extension"),
])
def test_malformed_gifs_are_refused(data, message):
    with pytest.raises(MalformedFileError, match=message):
        gif.strip(data)


def test_gif87a_without_global_table():
    data = make_gif(comment("x"), image(local_table=bytes(12)), version=b"87a",
                    global_table=False)
    cleaned = gif.strip(data)
    assert cleaned.startswith(b"GIF87a") and names(cleaned) == ["Image", "Trailer"]


# -- verification ------------------------------------------------------------------------

from metaex.core.verify import verify  # noqa: E402


def test_cleaned_gif_verifies_clean_with_matching_image_data():
    original = metadata_gif()
    cleaned = gif.strip(original)
    result = verify(cleaned, original)
    assert result.clean, (result.leftovers, result.problems)
    assert result.image_data_match is True
    assert {f.section for f in result.kept_by_design} == {"ICC profile", "Animation"}


def test_original_with_metadata_is_not_clean():
    result = verify(metadata_gif())
    assert not result.clean
    sections = {f.section for f in result.leftovers}
    assert {"Comments", "Plain text", "XMP", "Application extensions",
            "Trailing data", "GIF structure"} <= sections


@pytest.mark.parametrize("block,problem", [
    (comment("x"), "not allowed in a cleaned GIF"),
    (b"\x21\xf9\x04\xe0\x00\x00\x00\x00", "not in its minimal form"),
    (b"\x21\xf9\x04\x00\x00\x00\x00\x05extra\x00", "not in its minimal form"),
    (b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x02ab\x00", "more than the loop count"),
    (icc_extension(icc_profile() + b"extra"), "invalid or has extra bytes"),
])
def test_structure_leftovers(block, problem):
    result = verify(make_gif(block, image()))
    assert any(f.section == "GIF structure" and problem in f.value for f in result.leftovers)


def test_unused_graphic_control_is_a_leftover():
    result = verify(make_gif(image(), graphic_control()))
    assert any("does not apply to an image" in f.value for f in result.leftovers)


def test_second_loop_extension_is_a_leftover():
    result = verify(make_gif(loop(), loop(2, b"ANIMEXTS1.0"), image()))
    assert len([f for f in result.leftovers if f.section == "GIF structure"]) == 1


def test_changed_image_data_is_detected():
    original = metadata_gif()
    cleaned = bytearray(gif.strip(original))
    img = gif.walk_blocks(bytes(cleaned)).images[0]
    cleaned[img.data_start + 2] ^= 0x01  # one bit of LZW data
    result = verify(bytes(cleaned), original)
    assert result.image_data_match is False and not result.clean
    assert "image data differs from the original" in result.problems


def test_truncated_gif_is_a_problem():
    result = verify(gif.strip(metadata_gif())[:-1])
    assert not result.clean
    assert any("not a complete, well-formed GIF" in p for p in result.problems)
