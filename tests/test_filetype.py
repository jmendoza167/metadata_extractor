import io
import random
import tarfile
import zipfile

import pytest

from helpers import ole2

from metaex.core import filetype as ft
from metaex.core.filetype import detect


def ftyp(major: bytes, *compatible: bytes) -> bytes:
    body = major + b"\x00\x00\x00\x00" + b"".join(compatible)
    return (8 + len(body)).to_bytes(4, "big") + b"ftyp" + body + b"\x00" * 16


IMAGES = [
    (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00", ft.JPEG),
    (b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR", ft.PNG),
    (b"GIF87a\x01\x00\x01\x00", ft.GIF),
    (b"GIF89a\x01\x00\x01\x00", ft.GIF),
    (b"RIFF\x24\x00\x00\x00WEBPVP8 ", ft.WEBP),
    (b"II*\x00\x08\x00\x00\x00", ft.TIFF),
    (b"MM\x00*\x00\x00\x00\x08", ft.TIFF),
    (ftyp(b"heic", b"mif1", b"heic"), ft.HEIC),
    (ftyp(b"mif1", b"mif1", b"heic"), ft.HEIC),
    (ftyp(b"mif1", b"mif1", b"miaf"), ft.HEIF),
    (ftyp(b"avif", b"avif", b"mif1", b"miaf"), ft.AVIF),
    (ftyp(b"mif1", b"mif1", b"avif"), ft.AVIF),
    (b'<svg xmlns="http://www.w3.org/2000/svg"/>', ft.SVG),
]


@pytest.mark.parametrize("data,expected", IMAGES, ids=lambda v: getattr(v, "key", None))
def test_detects_images(data, expected):
    assert detect(data) == expected


def test_svg_after_bom_declaration_and_comments():
    data = (
        b"\xef\xbb\xbf"
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b"<!-- Generator: Some Editor 1.0 -->\n"
        b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
        b'<svg version="1.1">\n</svg>'
    )
    assert detect(data) == ft.SVG


def test_heic_tiff_avif_are_detected_but_not_supported():
    for t in (ft.HEIC, ft.HEIF, ft.TIFF, ft.AVIF):
        assert t.supported is False
    for t in (ft.JPEG, ft.PNG, ft.GIF, ft.WEBP, ft.SVG):
        assert t.supported is True


def test_filetype_fields():
    assert ft.JPEG.name == "JPEG"
    assert ft.JPEG.mime == "image/jpeg"
    assert ".jpg" in ft.JPEG.extensions


def test_unknown_data():
    assert detect(b"hello world") == ft.UNKNOWN
    assert detect(b"") == ft.UNKNOWN


# --- documents, audio, archives -----------------------------------------------


def make_zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def mpeg_frames(count: int = 2) -> bytes:
    # MPEG-1 Layer III, 128 kbit/s, 44.1 kHz, no padding: 417-byte frames.
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    return frame * count


def make_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        info = tarfile.TarInfo("hello.txt")
        info.size = 2
        tf.addfile(info, io.BytesIO(b"hi"))
    return buf.getvalue()


OOXML_CT = b'<?xml version="1.0"?><Types/>'

OTHER_FORMATS = [
    (b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n", ft.PDF),
    (make_zip({"a.txt": "hello"}), ft.ZIP),
    (make_zip({"[Content_Types].xml": OOXML_CT, "word/document.xml": "<w/>"}), ft.DOCX),
    (make_zip({"[Content_Types].xml": OOXML_CT, "xl/workbook.xml": "<x/>"}), ft.XLSX),
    (make_zip({"[Content_Types].xml": OOXML_CT, "ppt/presentation.xml": "<p/>"}), ft.PPTX),
    (make_zip({"mimetype": "application/vnd.oasis.opendocument.text", "content.xml": "<c/>"}), ft.ODT),
    (make_zip({"mimetype": "application/vnd.oasis.opendocument.spreadsheet"}), ft.ODS),
    (make_zip({"mimetype": "application/vnd.oasis.opendocument.presentation"}), ft.ODP),
    (make_zip({"mimetype": "application/epub+zip", "META-INF/container.xml": "<c/>"}), ft.EPUB),
    (make_zip({"META-INF/MANIFEST.MF": "Manifest-Version: 1.0\n", "A.class": "x"}), ft.JAR),
    (make_zip({"AndroidManifest.xml": "x", "classes.dex": "x"}), ft.APK),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504, ft.OLE2),
    (ole2.legacy_word(), ft.DOC),
    (ole2.legacy_excel(), ft.XLS),
    (ole2.legacy_powerpoint(), ft.PPT),
    (ole2.encrypted_office(), ft.ENCRYPTED_OFFICE),
    (b"ID3\x04\x00\x00\x00\x00\x00\x00" + mpeg_frames(1), ft.MP3),
    (mpeg_frames(2), ft.MP3),
    (b"RIFF\x24\x00\x00\x00WAVEfmt ", ft.WAV),
    (b"fLaC\x00\x00\x00\x22", ft.FLAC),
    (b"ID3\x03\x00\x00\x00\x00\x00\x00fLaC\x00\x00\x00\x22", ft.FLAC),
    (b"OggS\x00\x02" + b"\x00" * 22, ft.OGG),
    (b"RF64\xff\xff\xff\xffWAVEds64", ft.RF64),
    (b"BW64\xff\xff\xff\xffWAVEds64", ft.RF64),
    (b"FORM\x00\x00\x00\x10AIFFCOMM", ft.AIFF),
    (b"FORM\x00\x00\x00\x10AIFCFVER", ft.AIFF),
    (ftyp(b"isom", b"isom", b"iso2", b"mp41"), ft.MP4),
    (ftyp(b"qt  ", b"qt  "), ft.MOV),
    (ftyp(b"M4A ", b"M4A ", b"mp42", b"isom"), ft.M4A),
    (make_tar(), ft.TAR),
    (b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03", ft.GZIP),
    (b"7z\xbc\xaf\x27\x1c\x00\x04", ft.SEVEN_ZIP),
    (b"Rar!\x1a\x07\x00\xcf\x90", ft.RAR),
    (b"Rar!\x1a\x07\x01\x00\x33\x92", ft.RAR),
]


@pytest.mark.parametrize("data,expected", OTHER_FORMATS, ids=lambda v: getattr(v, "key", None))
def test_detects_other_formats(data, expected):
    assert detect(data) == expected


def test_in_scope_formats_are_marked_supported():
    for t in (ft.DOCX, ft.XLSX, ft.PPTX, ft.ODT, ft.ODS, ft.ODP, ft.MP3, ft.WAV, ft.FLAC,
              ft.ZIP, ft.TAR):
        assert t.supported is True, t.key
    for t in (ft.PDF, ft.OLE2, ft.DOC, ft.XLS, ft.PPT, ft.ENCRYPTED_OFFICE, ft.EPUB, ft.JAR,
              ft.APK, ft.OGG,
              ft.MP4, ft.MOV,
              ft.M4A, ft.GZIP, ft.SEVEN_ZIP, ft.RAR, ft.UNKNOWN):
        assert t.supported is False, t.key


def test_single_mpeg_frame_header_is_not_enough():
    assert detect(mpeg_frames(1)) == ft.UNKNOWN


def test_unknown_never_raises():
    assert detect(b"\x00" * 1000) == ft.UNKNOWN
    assert detect(b"PK\x03\x04garbage").key == "zip"


# --- extension mismatch -------------------------------------------------------

PNG_DATA = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR"
JPEG_DATA = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"


def test_mismatch_warning():
    result = detect(PNG_DATA, "photo.jpg")
    assert result.key == "png"
    assert result.warnings == ("extension .jpg but content is PNG",)


def test_matching_extension_has_no_warning():
    assert detect(PNG_DATA, "image.png").warnings == ()


@pytest.mark.parametrize("name", ["a.jpg", "a.jpeg", "a.jpe", "a.JPG", "a.JpEg", "dir/a.jpg"])
def test_equivalent_extensions_and_case(name):
    assert detect(JPEG_DATA, name).warnings == ()


@pytest.mark.parametrize("name", ["scan.tif", "scan.tiff", "SCAN.TIFF"])
def test_tiff_extensions(name):
    assert detect(b"II*\x00\x08\x00\x00\x00", name).warnings == ()


def test_no_filename_or_no_extension_means_no_warning():
    assert detect(PNG_DATA).warnings == ()
    assert detect(PNG_DATA, "photo").warnings == ()


def test_unknown_content_has_no_mismatch_warning():
    assert detect(b"plain text", "notes.jpg").warnings == ()


# --- edge cases -----------------------------------------------------------------


def test_every_known_type_has_a_sample():
    constants = {
        v.key for v in vars(ft).values()
        if isinstance(v, ft.FileType) and v is not ft.UNKNOWN
    }
    sampled = {t.key for _, t in IMAGES + OTHER_FORMATS}
    assert constants == sampled


@pytest.mark.parametrize("name", [None, "empty", "empty.jpg", "empty.zip"])
def test_empty_file(name):
    result = detect(b"", name)
    assert result == ft.UNKNOWN
    assert result.warnings == ()


@pytest.mark.parametrize(
    "truncated",
    [
        b"\xff\xd8",
        b"\x89PNG\r\n\x1a",
        b"GIF89",
        b"RIFF\x00\x00\x00\x00WEB",
        b"II*",
        b"MM\x00",
        b"%PDF",
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a",
        b"fLa",
        b"Ogg",
        b"\x1f\x8b",
        b"7z\xbc\xaf\x27",
        b"Rar!\x1a\x07",
        b"ID3\x04\x00",
        b"\x00\x00\x00\x18ftyphei",
        b"<svg",
        b"\xff\xfb\x90",
    ],
)
def test_truncated_headers_are_unknown(truncated):
    assert detect(truncated) == ft.UNKNOWN


def test_truncated_tar_header_is_not_tar():
    assert detect(make_tar()[:511]) != ft.TAR


def test_tar_with_bad_checksum_is_not_tar():
    data = bytearray(make_tar())
    data[0] ^= 0xFF  # change the member name; checksum no longer matches
    assert detect(bytes(data)) != ft.TAR


def test_second_mpeg_frame_cut_short():
    assert detect(mpeg_frames(2)[:419]) == ft.UNKNOWN


def test_mpeg_frames_with_different_sample_rates_are_rejected():
    first = b"\xff\xfb\x90\x00" + b"\x00" * 413
    second = b"\xff\xfb\x94\x00"  # same bitrate, 48 kHz
    assert detect(first + second) == ft.UNKNOWN


def test_id3_with_size_past_end_of_data_is_still_mp3():
    assert detect(b"ID3\x03\x00\x00\x7f\x7f\x7f\x7f") == ft.MP3


@pytest.mark.parametrize("cut", [4, 30, 60])
def test_truncated_zip_is_plain_zip_with_warning(cut):
    data = make_zip({"[Content_Types].xml": OOXML_CT, "word/document.xml": "<w/>"})
    result = detect(data[:cut])
    assert result.key == "zip"
    assert result.warnings
    assert "not examined" in result.warnings[0]


@pytest.mark.parametrize("cut", [-10, -22])
def test_truncated_office_file_is_still_recognized(cut):
    """Its entries are still there: it is a damaged DOCX (and the DOCX parser
    then says what is wrong), not an unknown ZIP."""
    data = make_zip({"[Content_Types].xml": OOXML_CT, "word/document.xml": "<w/>"})
    result = detect(data[:cut])
    assert result.key == "docx"
    assert result.warnings == ("ZIP directory could not be read (the file may be truncated)",)


def test_truncated_opendocument_file_is_still_recognized():
    data = make_zip({"mimetype": "application/vnd.oasis.opendocument.text", "content.xml": "<c/>"})
    assert detect(data[:-22]).key == "odt"


def test_zip_with_too_many_entries_is_not_examined(monkeypatch):
    monkeypatch.setattr(ft, "ZIP_MAX_ENTRIES", 1)
    data = make_zip({"[Content_Types].xml": OOXML_CT, "word/document.xml": "<w/>"})
    result = detect(data)
    assert result.key == "zip"
    assert "2 entries" in result.warnings[0]


def test_corrupt_central_directory_keeps_the_type_found_in_the_entries():
    data = bytearray(make_zip({"[Content_Types].xml": OOXML_CT, "word/document.xml": "<w/>"}))
    cd = data.find(b"PK\x01\x02")
    data[cd : cd + 4] = b"XXXX"
    result = detect(bytes(data))
    assert result.key == "docx"
    assert result.warnings
    second = data.find(b"PK\x03\x04", 4)
    data[second : second + 4] = b"XXXX"  # word/document.xml can't be found: plain ZIP
    result = detect(bytes(data))
    assert result.key == "zip" and "not examined" in result.warnings[0]


@pytest.mark.parametrize(
    "entries",
    [
        {"word/document.xml": "<w/>"},  # no [Content_Types].xml
        {"[Content_Types].xml": OOXML_CT, "readme.txt": "x"},  # no word/xl/ppt
        {"[Content_Types].xml": OOXML_CT, "word/a.xml": "", "xl/b.xml": ""},  # ambiguous
        {"[Content_Types].xml": OOXML_CT, "wordy/document.xml": "<w/>"},  # prefix trick
        {"[content_types].xml": OOXML_CT, "word/document.xml": "<w/>"},  # wrong case
        {"mimetype": "application/x-something-else", "word/document.xml": ""},
        {"mimetype": "application/vnd.oasis.opendocument.text" + " " * 300},  # oversized
        {"mimetype": b"\xff\xfe\x00bad"},  # not ASCII
    ],
)
def test_zip_that_only_looks_like_an_office_file_is_plain_zip(entries):
    assert detect(make_zip(entries)) == ft.ZIP


def test_empty_zip():
    assert detect(make_zip({})) == ft.ZIP


def test_unknown_ftyp_brand_is_unknown():
    assert detect(ftyp(b"zzzz", b"yyyy")) == ft.UNKNOWN


def test_ftyp_with_tiny_box_size_is_unknown():
    assert detect(b"\x00\x00\x00\x04ftypheic" + b"\x00" * 8) == ft.UNKNOWN


@pytest.mark.parametrize(
    "data",
    [
        b"<!-- never closed <svg>",
        b"<!DOCTYPE svg [ never closed <svg>",
        b"<?xml version='1.0'?><html><svg/></html>",
        b"<svgfoo/>",
        b"Hello <svg/>",
        b"<!--" + b"x" * 5000 + b"--><svg/>",  # root element beyond 4 KB
    ],
)
def test_not_svg(data):
    assert detect(data) == ft.UNKNOWN


@pytest.mark.parametrize(
    "data",
    [
        b"  \n\t<svg>",
        b"<svg:svg xmlns:svg='http://www.w3.org/2000/svg'/>",
        b"<!DOCTYPE svg [ <!ENTITY a 'b'> ]>\n<svg/>",
        b"\xff\xfe" + "<?xml version='1.0'?><svg/>".encode("utf-16-le"),
        b"\xfe\xff" + "<svg/>".encode("utf-16-be"),
    ],
)
def test_svg_variants(data):
    assert detect(data) == ft.SVG


def test_random_bytes_never_raise():
    rng = random.Random(1234)
    prefixes = [b"", b"PK\x03\x04", b"PK\x05\x06", b"ID3\x03\x00", b"RIFF",
                b"\x00\x00\x00\x20ftyp", b"\xff\xfb", b"<", b"\xef\xbb\xbf<"]
    for _ in range(600):
        prefix = rng.choice(prefixes)
        data = prefix + rng.randbytes(rng.randrange(0, 3000))
        result = detect(data, "x.bin")
        assert isinstance(result, ft.FileType)


@pytest.mark.parametrize(
    "data,name,warning",
    [
        (make_zip({"[Content_Types].xml": OOXML_CT, "word/d.xml": ""}), "report.zip",
         "extension .zip but content is DOCX"),
        (make_zip({"a.txt": "x"}), "report.docx", "extension .docx but content is ZIP"),
        (mpeg_frames(2), "song.wav", "extension .wav but content is MP3"),
        (b"<svg/>", "logo.PNG", "extension .PNG but content is SVG"),
        (b"%PDF-1.4", "invoice.pdf.exe", "extension .exe but content is PDF"),
    ],
)
def test_wrong_extensions(data, name, warning):
    assert warning in detect(data, name).warnings


def test_mismatch_warning_is_added_after_zip_warning():
    data = make_zip({"a": "b"})[:30]
    result = detect(data, "a.docx")
    assert len(result.warnings) == 2
    assert result.warnings[1] == "extension .docx but content is ZIP"


def test_zip_with_data_in_front_is_zip_with_a_warning():
    data = b"MZ self-extractor stub " * 10 + make_zip({"a.txt": "hello"})
    result = detect(data)
    assert result.key == "zip"
    assert result.warnings == ("data before the start of the ZIP archive",)


def test_office_file_with_data_in_front_keeps_its_type():
    data = b"junk in front\n" + make_zip({"[Content_Types].xml": OOXML_CT,
                                          "word/document.xml": "<w/>"})
    assert detect(data).key == "docx"


@pytest.mark.parametrize("data", [
    b"text that ends with PK\x05\x06" + bytes(18),
    b"text" + b"PK\x05\x06" + bytes(8) + (100).to_bytes(4, "little") + bytes(6),
    b"x" * 100 + b"PK\x01\x02" + bytes(40) + b"PK\x05\x06" + bytes(6)
    + (46).to_bytes(4, "little") + (0).to_bytes(4, "little") + bytes(2),
])
def test_end_record_alone_is_not_zip(data):
    assert detect(data) == ft.UNKNOWN
