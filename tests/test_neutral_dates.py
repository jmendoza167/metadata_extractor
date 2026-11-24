"""Required dates are replaced with neutral ones that keep their order.

PowerPoint (modern comments) and OpenDocument (tracked changes) require a
date. strip writes 2000-01-01T00:00:00 plus one second per item, ranked by
the original dates, because programs sort by date and reorder items that
share one.
"""

import re

from helpers.odf import DECLS, Document, content_xml
from helpers.ooxml import A_NS, AUTHOR_GUID, DECL, P188_NS, add_ppt_comments, pptx
from metaex.core.odf import open_odf
from metaex.core.ooxml import (is_neutral_date, is_neutral_date_range, neutral_dates,
                               open_package)
from metaex.core.verify import verify
from metaex.formats import odt as odt_format
from metaex.formats import pptx as pptx_format

# -- neutral_dates --------------------------------------------------------------------------


def test_steps_follow_the_original_order():
    originals = ["2024-05-03T10:00:00.000", "2024-05-01T09:00:00.000", "2024-05-02T08:00:00.000"]
    assert neutral_dates(originals) == ["2000-01-01T00:00:02.000", "2000-01-01T00:00:00.000",
                                        "2000-01-01T00:00:01.000"]


def test_equal_dates_keep_document_order():
    assert neutral_dates(["2024-05-01T10:00:00Z"] * 3, millis=False) == [
        "2000-01-01T00:00:00", "2000-01-01T00:00:01", "2000-01-01T00:00:02"]


def test_time_zones_and_fractions_are_compared_as_instants():
    # 10:00+02:00 is 08:00Z, before 09:00Z; .5 seconds after .25 seconds
    originals = ["2024-05-01T10:00:00+02:00", "2024-05-01T09:00:00Z",
                 "2024-05-01T09:00:00.5Z", "2024-05-01T09:00:00.25Z"]
    assert neutral_dates(originals, millis=False) == [
        "2000-01-01T00:00:00", "2000-01-01T00:00:01", "2000-01-01T00:00:03",
        "2000-01-01T00:00:02"]


def test_unreadable_dates_fall_back_to_document_order():
    assert neutral_dates(["2024-05-03T10:00:00", "yesterday", "2024-05-01T10:00:00"],
                         millis=False) == ["2000-01-01T00:00:00", "2000-01-01T00:00:01",
                                           "2000-01-01T00:00:02"]


def test_neutral_dates_are_stable():
    once = neutral_dates(["2024-05-03T10:00:00.000", "2024-05-01T09:00:00.000"])
    assert neutral_dates(once) == once


def test_more_than_a_day_of_steps():
    out = neutral_dates([f"2024-05-01T10:00:00.{i:06d}" for i in range(86_401)])
    assert out[-1] == "2000-01-02T00:00:00.000" and all(is_neutral_date(d) for d in out)


def test_recognizing_neutral_dates():
    assert is_neutral_date("2000-01-01T00:00:00") and is_neutral_date("2000-01-01T00:00:59.000")
    assert not is_neutral_date("2000-01-01T00:00:00Z")
    assert not is_neutral_date("2000-01-31T00:00:00")  # more than MAX_NEUTRAL_SECONDS
    assert not is_neutral_date("2024-05-01T10:00:00")
    assert is_neutral_date_range("2000-01-01T00:00:00.000")
    assert is_neutral_date_range("2000-01-01T00:00:00.000 (3 times)")
    assert is_neutral_date_range("2000-01-01T00:00:00 to 2000-01-01T00:00:02 (3 dates)")
    assert not is_neutral_date_range("2000-01-01T00:00:00 to 2024-05-01T10:00:00 (2 dates)")
    assert not is_neutral_date_range("")


# -- PowerPoint modern comments ---------------------------------------------------------------

MODERN_PART = "ppt/comments/modernComment_100_ABCDEF12.xml"


def modern_comments(threads: list[tuple[str, list[str]]]) -> str:
    """A modern comment part: (comment date, [reply dates]) per thread."""
    cms = []
    for n, (created, replies) in enumerate(threads):
        reply_xml = "".join(
            f'<p188:reply id="{{EEEEEEEE-0000-1111-2222-{n:06d}{r:06d}}}" '
            f'authorId="{AUTHOR_GUID}" created="{date}"><p188:txBody><a:bodyPr/><a:lstStyle/>'
            f"<a:p><a:r><a:t>Reply {n}.{r}</a:t></a:r></a:p></p188:txBody></p188:reply>"
            for r, date in enumerate(replies))
        cms.append(
            f'<p188:cm id="{{DDDDDDDD-0000-1111-2222-{n:012d}}}" authorId="{AUTHOR_GUID}" '
            f'created="{created}">'
            + (f"<p188:replyLst>{reply_xml}</p188:replyLst>" if replies else "")
            + f"<p188:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Comment {n}</a:t></a:r>"
            "</a:p></p188:txBody></p188:cm>")
    return (f'{DECL}<p188:cmLst xmlns:a="{A_NS}" xmlns:p188="{P188_NS}">'
            + "".join(cms) + "</p188:cmLst>")


def created_dates(xml: bytes) -> list[str]:
    return re.findall(rb'created="([^"]*)"', xml)


def test_powerpoint_comments_keep_their_order():
    threads = [("2024-05-01T10:00:00.000", ["2024-05-01T12:00:00.000", "2024-05-01T11:00:00.000"]),
               ("2024-05-01T09:00:00.000", []),
               ("2024-05-01T10:00:00.000", ["2024-05-02T08:00:00.000"])]
    pkg = add_ppt_comments(pptx())
    pkg.parts[MODERN_PART] = modern_comments(threads).encode("utf-8")
    original = pkg.build()
    cleaned = pptx_format.strip(original)
    before = created_dates(open_package(original).read(MODERN_PART))
    after = created_dates(open_package(cleaned).read(MODERN_PART))
    assert after == [b"2000-01-01T00:00:01.000", b"2000-01-01T00:00:04.000",
                     b"2000-01-01T00:00:03.000", b"2000-01-01T00:00:00.000",
                     b"2000-01-01T00:00:02.000", b"2000-01-01T00:00:05.000"]
    # sorting by the new dates gives the same order as sorting by the old ones
    rank = sorted(range(len(before)), key=lambda i: (before[i], i))
    assert sorted(range(len(after)), key=lambda i: after[i]) == rank
    assert len(set(after)) == len(after)
    result = verify(cleaned, original)
    assert result.clean and result.image_data_match, (result.leftovers, result.problems)
    assert pptx_format.strip(cleaned) == cleaned  # stripping again changes nothing


def test_the_comments_section_shows_the_neutral_range():
    pkg = add_ppt_comments(pptx())
    pkg.parts[MODERN_PART] = modern_comments(
        [("2024-05-01T10:00:00.000", ["2024-05-01T11:00:00.000"])]).encode("utf-8")
    cleaned = open_package(pptx_format.strip(pkg.build()))
    dates = pptx_format.comments_section(cleaned).get("dates").value
    assert dates == "2000-01-01T00:00:00.000 to 2000-01-01T00:00:01.000 (2 dates)"


# -- OpenDocument tracked changes -----------------------------------------------------------


def tracked_changes(dates: list[str]) -> str:
    regions = "".join(
        f'<text:changed-region xml:id="ct{n}" text:id="ct{n}"><text:insertion>'
        f"<office:change-info><dc:creator>Jane Doe</dc:creator><dc:date>{date}</dc:date>"
        "</office:change-info></text:insertion></text:changed-region>"
        for n, date in enumerate(dates))
    marks = "".join(f'<text:change-start text:change-id="ct{n}"/>word {n} '
                    f'<text:change-end text:change-id="ct{n}"/>' for n in range(len(dates)))
    return (f'<text:tracked-changes text:track-changes="true">{regions}</text:tracked-changes>'
            f"<text:p>{marks}</text:p>")


def test_opendocument_tracked_changes_keep_their_order():
    dates = ["2024-05-03T10:00:00", "2024-05-01T10:00:00.123456789", "2024-05-02T10:00:00",
             "2024-05-01T10:00:00.123456789"]
    doc = Document("odt", tracked_changes(dates))
    original = doc.build()
    cleaned = odt_format.strip(original)
    content = open_odf(cleaned).read("content.xml")
    assert re.findall(rb"<dc:date>([^<]*)</dc:date>", content) == [
        b"2000-01-01T00:00:03", b"2000-01-01T00:00:00", b"2000-01-01T00:00:02",
        b"2000-01-01T00:00:01"]
    assert b"Jane" not in content
    result = verify(cleaned, original)
    assert result.clean and result.image_data_match, (result.leftovers, result.problems)
    assert odt_format.strip(cleaned) == cleaned


def test_helpers_are_what_this_test_expects():
    assert "xmlns:dc=" in DECLS and "<office:text>" in content_xml("odt")
