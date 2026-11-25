"""ICC profiles that hold more than a color profile.

Regression test: a kept ICC profile was copied byte for byte once it had
an 'acsp' signature, so a private tag (or bytes outside every tag) could
carry a name, an e-mail address or a position into the cleaned file, which
verify called CLEAN. A profile is now kept only if every tag is a known
color tag and every byte belongs to the header, the tag table or a tag;
otherwise show and risk report it, strip removes it and verify fails."""

import struct

import pytest

from helpers import gif as gif_helpers
from helpers import webp as webp_helpers
from helpers.jpeg import icc_profile, icc_segments, make_jpeg
from helpers.png import iccp, make_png
from metaex.core import icc
from metaex.core.risk import HIGH, classify
from metaex.core.verify import verify
from metaex.formats import gif, jpeg, png, webp

SECRET = b"Jane Doe jane.doe@example.com 48.8584 N 2.2945 E"


def with_private_tag() -> bytes:
    good = icc_profile()
    count = struct.unpack(">I", good[128:132])[0]
    table_end = 132 + 12 * count
    shift = 12  # one more tag table entry
    entries = [good[132 + 12 * i:144 + 12 * i] for i in range(count)]
    moved = [e[:4] + struct.pack(">I", struct.unpack(">I", e[4:8])[0] + shift) + e[8:]
             for e in entries]
    body = good[table_end:]
    private = b"zzzz" + struct.pack(">II", len(good) + shift, len(SECRET))
    profile = bytearray(good[:128] + struct.pack(">I", count + 1) + b"".join(moved) + private
                        + body + SECRET)
    struct.pack_into(">I", profile, 0, len(profile))
    return bytes(profile)


def with_bytes_outside_tags() -> bytes:
    profile = bytearray(icc_profile() + SECRET)
    struct.pack_into(">I", profile, 0, len(profile))
    return bytes(profile)


def with_reserved_header_bytes() -> bytes:
    profile = bytearray(icc_profile())
    profile[100:110] = b"Jane Doe!!"
    return bytes(profile)


BAD = {"private tag": with_private_tag, "bytes outside tags": with_bytes_outside_tags,
       "reserved header": with_reserved_header_bytes}


def test_a_real_profile_is_accepted_and_kept():
    assert icc.profile_problem(icc_profile()) is None
    good = make_jpeg(*icc_segments(icc_profile()))
    cleaned = jpeg.strip(good)
    assert b"ICC_PROFILE" in cleaned and verify(cleaned, good).clean


@pytest.mark.parametrize("make", BAD.values(), ids=BAD.keys())
def test_profile_problems_are_found(make):
    assert icc.profile_problem(make()) is not None


@pytest.mark.parametrize("make", BAD.values(), ids=BAD.keys())
def test_png(make):
    data = make_png(before_idat=(iccp(make()),))
    section = next(s for s in png.parse(data)["sections"] if s.key == "icc")
    field = section.get("unchecked_data")
    assert field is not None and classify("icc", field.key) == HIGH
    cleaned = png.strip(data)
    assert b"iCCP" not in cleaned and SECRET not in cleaned
    assert verify(cleaned, data).clean
    assert not verify(data).clean  # verify fails on its own


@pytest.mark.parametrize("make", BAD.values(), ids=BAD.keys())
def test_jpeg(make):
    data = make_jpeg(*icc_segments(make()))
    cleaned = jpeg.strip(data)
    assert b"ICC_PROFILE" not in cleaned
    assert verify(cleaned, data).clean
    assert not verify(data).clean


@pytest.mark.parametrize("make", BAD.values(), ids=BAD.keys())
def test_webp(make):
    data = webp_helpers.extended(webp_helpers.iccp(make()),
                                 webp_helpers.chunk("VP8L", webp_helpers.vp8l(4, 4)))
    cleaned = webp.strip(data)
    assert b"ICCP" not in cleaned and SECRET not in cleaned
    assert verify(cleaned, data).clean
    assert not verify(data).clean


@pytest.mark.parametrize("make", BAD.values(), ids=BAD.keys())
def test_gif(make):
    data = gif_helpers.make_gif(gif_helpers.icc_extension(make()), gif_helpers.image())
    cleaned = gif.strip(data)
    assert b"ICCRGBG1012" not in cleaned and SECRET not in cleaned
    assert verify(cleaned, data).clean
    assert not verify(data).clean
