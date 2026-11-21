import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import zipfile

import pytest

from metaex import cli
from metaex.commands import show as show_cmd

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32
FLAC = b"fLaC" + b"\x00" * 64  # a format metaex plans to support but can't read yet


def run_show(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "metaex", "show", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_show_text_report(tmp_path):
    p = tmp_path / "song.flac"
    p.write_bytes(FLAC)
    result = run_show(str(p))
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    out = " ".join(result.stdout.split())  # ignore line wrapping
    for expected in ("File type", "FLAC", "planned (not implemented yet)",
                     "File system", "Hashes", "SHA-256", "was NOT examined"):
        assert expected in out


def test_show_json_report(tmp_path):
    p = tmp_path / "image.png"
    p.write_bytes(PNG)
    result = run_show(str(p), "--json")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["sections"]["file_type"]["fields"]["name"]["value"] == "PNG"
    assert data["sections"]["file_system"]["fields"]["size_bytes"]["value"] == len(PNG)


def test_show_missing_file_is_one_line_error(tmp_path):
    result = run_show(str(tmp_path / "missing.png"))
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("metaex: error:")
    assert len(result.stderr.strip().splitlines()) == 1
    assert "Traceback" not in result.stderr


# --- edge cases -----------------------------------------------------------------


def make_zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def make_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        info = tarfile.TarInfo("a.txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"a"))
    return buf.getvalue()


def show_json(path) -> dict:
    result = run_show(str(path), "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def field(data: dict, section: str, key: str):
    return data["sections"][section]["fields"][key]["value"]


PLANNED = "planned (not implemented yet)"
UNRECOGNIZED = "not supported (unrecognized format)"


def test_show_jpeg_is_examined(tmp_path):
    from helpers.jpeg import make_jpeg

    p = tmp_path / "photo.jpg"
    p.write_bytes(make_jpeg())
    data = show_json(p)
    assert field(data, "file_type", "support") == "supported"
    assert field(data, "file_type", "embedded_metadata") == "examined"
    assert not any("NOT examined" in w for w in data["warnings"])


def test_show_jpeg_sections_in_order(tmp_path):
    from helpers.jpeg import sample_photo

    p = tmp_path / "photo.jpg"
    p.write_bytes(sample_photo(trailing=b"hidden"))
    data = show_json(p)
    assert list(data["sections"]) == [
        "file_type", "exif", "gps", "thumbnail", "xmp", "trailing",
        "file_system", "hashes",
    ]
    assert field(data, "gps", "coordinates") == "48.858367, 2.294481"
    assert field(data, "exif", "Make") == "ExampleCam"
    assert data["warnings"][0].startswith("CRITICAL")
    critical = [w for w in data["warnings"] if w.startswith("CRITICAL")]
    assert data["warnings"][: len(critical)] == critical


def test_duplicate_warnings_are_shown_once(tmp_path):
    from helpers.jpeg import SAMPLE_XMP, extended_xmp_segments, make_jpeg, xmp_segment

    p = tmp_path / "photo.jpg"
    p.write_bytes(make_jpeg(xmp_segment(SAMPLE_XMP), *extended_xmp_segments(SAMPLE_XMP)))
    warnings = show_json(p)["warnings"]
    gps = [w for w in warnings if "XMP contains GPS data" in w]
    assert len(gps) == 1
    assert gps[0].startswith("CRITICAL")
    assert len(warnings) == len(set(warnings))


def test_structure_only_with_flag(tmp_path):
    from helpers.jpeg import sample_photo

    p = tmp_path / "photo.jpg"
    p.write_bytes(sample_photo())
    assert "structure" not in show_json(p)["sections"]
    result = run_show(str(p), "--json", "--structure")
    segments = json.loads(result.stdout)["sections"]["structure"]["fields"]
    labels = [f["label"] for f in segments.values()]
    assert labels[:3] == ["SOI", "APP1", "APP1"]
    assert labels[-1] == "EOI"


def test_show_malformed_jpeg_is_partly_examined(tmp_path):
    p = tmp_path / "broken.jpg"
    p.write_bytes(JPEG)  # a JFIF header followed by zeros
    data = show_json(p)
    assert field(data, "file_type", "embedded_metadata") == "partly examined (file is malformed)"
    assert any("malformed JPEG" in w for w in data["warnings"])


@pytest.mark.parametrize(
    "name,content,type_name,support",
    [
        ("song.flac", FLAC, "FLAC", PLANNED),
        ("sound.wav", b"RIFF\x24\x00\x00\x00WAVEfmt " + bytes(20), "WAV", PLANNED),
        ("letter.docx", make_zip({"[Content_Types].xml": "<T/>", "word/document.xml": "<w/>"}),
         "DOCX", PLANNED),
        ("bundle.zip", make_zip({"a.txt": "a"}), "ZIP", PLANNED),
        ("backup.tar", make_tar(), "TAR", PLANNED),
        ("paper.pdf", b"%PDF-1.7\n", "PDF", "not supported"),
        ("scan.heic", b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic", "HEIC",
         "not supported"),
        ("notes.txt", b"just some text\n", "unknown", UNRECOGNIZED),
        ("empty.bin", b"", "unknown", UNRECOGNIZED),
    ],
)
def test_show_generated_files(tmp_path, name, content, type_name, support):
    p = tmp_path / name
    p.write_bytes(content)
    data = show_json(p)
    assert field(data, "file_type", "name") == type_name
    assert field(data, "file_type", "support") == support
    assert field(data, "file_type", "embedded_metadata") == "not examined"
    assert field(data, "file_system", "size_bytes") == len(content)
    assert any("NOT examined" in w for w in data["warnings"])


def test_json_has_expected_keys(tmp_path):
    p = tmp_path / "image.png"
    p.write_bytes(PNG)
    data = show_json(p)
    assert list(data) == ["metaex_version", "subject", "warnings", "sections"]
    assert data["subject"] == str(p)
    assert list(data["sections"])[0] == "file_type"
    assert {"file_type", "file_system", "hashes"} <= set(data["sections"])
    assert set(data["sections"]["file_type"]["fields"]) >= {
        "name", "mime", "extensions", "support", "embedded_metadata"}
    assert set(data["sections"]["file_system"]["fields"]) >= {
        "path", "size_bytes", "size", "modified", "accessed", "created", "permissions"}
    assert list(data["sections"]["hashes"]["fields"]) == ["md5", "sha1", "sha256"]
    for f in data["sections"]["file_system"]["fields"].values():
        assert set(f) == {"label", "value", "sensitive", "note"}


def test_json_hashes_match_file(tmp_path):
    p = tmp_path / "image.png"
    p.write_bytes(PNG)
    data = show_json(p)
    assert field(data, "hashes", "sha256") == hashlib.sha256(PNG).hexdigest()
    assert field(data, "hashes", "md5") == hashlib.md5(PNG).hexdigest()


def test_extension_mismatch_warning_in_text_and_json(tmp_path):
    p = tmp_path / "holiday.jpg"
    p.write_bytes(PNG)
    text = run_show(str(p))
    assert text.returncode == 0
    assert "! extension .jpg but content is PNG" in text.stdout
    assert text.stdout.index("Warnings") < text.stdout.index("File type")
    assert "extension .jpg but content is PNG" in show_json(p)["warnings"]


def assert_one_line_error(result, *fragments):
    assert result.returncode == 1
    assert result.stdout == ""
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1, result.stderr
    assert lines[0].startswith("metaex: error:")
    for fragment in fragments:
        assert fragment in lines[0]


def test_missing_file(tmp_path):
    assert_one_line_error(run_show(str(tmp_path / "nope.jpg")), "no such file")


def test_directory(tmp_path):
    assert_one_line_error(run_show(str(tmp_path)), "is a directory")


def test_too_large_file_with_small_max_size(tmp_path):
    p = tmp_path / "big.bin"
    p.write_bytes(b"x" * 5000)
    # 0.001 MiB = 1048 bytes
    assert_one_line_error(run_show(str(p), "--max-size", "0.001"), "too large", "--max-size")
    assert run_show(str(p), "--max-size", "0.01").returncode == 0


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "nan", "inf", ""])
def test_invalid_max_size_is_usage_error(tmp_path, bad):
    p = tmp_path / "a.png"
    p.write_bytes(PNG)
    result = run_show(str(p), "--max-size", bad)
    assert result.returncode == 2
    assert "--max-size" in result.stderr


def test_debug_shows_traceback(tmp_path):
    result = run_show(str(tmp_path), "--debug")
    assert result.returncode == 1
    assert "Traceback" in result.stderr
    assert "metaex: error:" in result.stderr


def test_random_bytes_do_not_crash(tmp_path):
    p = tmp_path / "noise.dat"
    p.write_bytes(os.urandom(4096))
    result = run_show(str(p))
    assert result.returncode == 0, result.stderr


def test_non_ascii_filename_on_limited_console(tmp_path):
    name = "caf" + chr(0xE9) + "_" + chr(0x6F22) + chr(0x5B57) + ".png"
    p = tmp_path / name
    p.write_bytes(PNG)
    env = dict(os.environ, PYTHONIOENCODING="ascii")
    result = subprocess.run(
        [sys.executable, "-m", "metaex", "show", str(p)],
        capture_output=True, timeout=60, env=env,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout.decode("ascii")
    assert "caf" + chr(92) + "xe9" in out


def test_internal_errors_are_one_line_without_debug(tmp_path, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(show_cmd, "build_report", boom)
    p = tmp_path / "a.png"
    p.write_bytes(PNG)
    assert cli.main(["show", str(p)]) == 1
    err = capsys.readouterr().err
    assert err.strip() == ("metaex: error: internal error: RuntimeError: unexpected "
                           "(run with --debug for details)")

    assert cli.main(["show", str(p), "--debug"]) == 1
    assert "Traceback" in capsys.readouterr().err


# --- --extract-thumbnail ------------------------------------------------------

def jpeg_with_thumbnail():
    from helpers.jpeg import app, make_jpeg
    from helpers.tiff import ASCII, SHORT, build_tiff, exif_payload

    thumb = make_jpeg(width=160, height=120)
    tiff = build_tiff([(0x010F, ASCII, "Cam")], ifd1=[(0x0103, SHORT, 6)], thumbnail=thumb)
    return make_jpeg(app(1, exif_payload(tiff)), width=1600, height=1200), thumb


def test_extract_thumbnail(tmp_path):
    photo, thumb = jpeg_with_thumbnail()
    src = tmp_path / "photo.jpg"
    src.write_bytes(photo)
    out = tmp_path / "thumb.jpg"
    result = run_show(str(src), "--extract-thumbnail", str(out), "--json")
    assert result.returncode == 0, result.stderr
    json.loads(result.stdout)
    assert out.read_bytes() == thumb
    assert f"saved embedded thumbnail ({len(thumb)} bytes)" in result.stderr


def test_extract_thumbnail_refuses_to_overwrite(tmp_path):
    photo, _ = jpeg_with_thumbnail()
    src = tmp_path / "photo.jpg"
    src.write_bytes(photo)
    out = tmp_path / "thumb.jpg"
    out.write_bytes(b"precious")
    assert_one_line_error(run_show(str(src), "--extract-thumbnail", str(out)),
                          "already exists", "refusing to overwrite")
    assert out.read_bytes() == b"precious"
    assert_one_line_error(run_show(str(src), "--extract-thumbnail", str(src)), "already exists")
    assert src.read_bytes() == photo


def test_extract_thumbnail_when_there_is_none(tmp_path):
    from helpers.jpeg import make_jpeg

    src = tmp_path / "plain.jpg"
    src.write_bytes(make_jpeg())
    out = tmp_path / "thumb.jpg"
    assert_one_line_error(run_show(str(src), "--extract-thumbnail", str(out)),
                          "no embedded EXIF thumbnail")
    assert not out.exists()


def test_extract_thumbnail_from_non_jpeg(tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(PNG)
    assert_one_line_error(run_show(str(src), "--extract-thumbnail", str(tmp_path / "t.jpg")),
                          "only supported for JPEG")


def test_main_returns_zero_in_process(tmp_path, capsys):
    p = tmp_path / "a.png"
    p.write_bytes(PNG)
    assert cli.main(["show", str(p), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["subject"] == str(p)


# --- PNG ------------------------------------------------------------------------


def _png_with_metadata():
    from helpers.jpeg import SAMPLE_XMP, icc_profile
    from helpers.png import chunk, exif_chunk, iccp, itxt, make_png, text, time_chunk
    from helpers.tiff import ASCII, RATIONAL, build_tiff

    tiff = build_tiff([(0x010F, ASCII, "ExampleCam")],
                      gps=[(0x0001, ASCII, "N"), (0x0002, RATIONAL, [(48, 1), (51, 1), (0, 1)]),
                           (0x0003, ASCII, "E"), (0x0004, RATIONAL, [(2, 1), (17, 1), (0, 1)])])
    return make_png(
        text("Author", "Jane Doe"), text("parameters", "a cat, Seed: 42"),
        itxt("XML:com.adobe.xmp", SAMPLE_XMP), time_chunk(), chunk("caBX", bytes(50)),
        chunk("prVt", b"private"),
        before_idat=(exif_chunk(tiff), iccp(icc_profile()), chunk("gAMA", bytes(4))),
        trailing=b"hidden after IEND")


def test_show_png_sections(tmp_path):
    p = tmp_path / "image.png"
    p.write_bytes(_png_with_metadata())
    data = show_json(p)
    assert field(data, "file_type", "name") == "PNG"
    assert field(data, "file_type", "embedded_metadata") == "examined"
    assert list(data["sections"]) == [
        "file_type", "text", "ai", "xmp", "exif", "gps", "time", "icc", "color",
        "content_credentials", "unknown", "trailing", "file_system", "hashes"]
    assert field(data, "text", "Author") == "Jane Doe"
    assert field(data, "ai", "parameters") == "a cat, Seed: 42"
    assert data["sections"]["ai"]["title"] == "AI generation data"
    assert field(data, "time", "tIME") == "2024-05-01T12:30:00Z"
    assert data["warnings"][0].startswith("CRITICAL")
    assert "structure" not in data["sections"]


def test_show_png_structure(tmp_path):
    p = tmp_path / "image.png"
    p.write_bytes(_png_with_metadata())
    result = run_show(str(p), "--structure")
    assert result.returncode == 0, result.stderr
    assert "PNG structure" in result.stdout
    assert "offset 8, 13 bytes, critical" in result.stdout


def test_show_malformed_png_is_partly_examined(tmp_path):
    p = tmp_path / "broken.png"
    p.write_bytes(PNG)  # an IHDR with a bad CRC followed by zeros
    data = show_json(p)
    assert field(data, "file_type", "embedded_metadata") == "partly examined (file is malformed)"
    assert any("bad CRC" in w for w in data["warnings"])
    assert any("malformed PNG" in w for w in data["warnings"])


# -- GIF -----------------------------------------------------------------------------------


def _gif_with_metadata():
    from helpers.gif import (application, comment, graphic_control, icc_extension, image,
                             loop, make_gif, plain_text, xmp_extension)
    from helpers.jpeg import SAMPLE_XMP, icc_profile

    return make_gif(comment("Made by Jane Doe"), loop(0), icc_extension(icc_profile()),
                    xmp_extension(SAMPLE_XMP), graphic_control(), image(),
                    plain_text("hello"), graphic_control(), image(seed=4),
                    application(b"MGK8BIM0000", b"x" * 30), trailing=b"after the trailer")


def test_show_gif_sections(tmp_path):
    p = tmp_path / "anim.gif"
    p.write_bytes(_gif_with_metadata())
    data = show_json(p)
    assert field(data, "file_type", "name") == "GIF"
    assert field(data, "file_type", "embedded_metadata") == "examined"
    assert field(data, "file_type", "support") == "supported"
    assert list(data["sections"]) == [
        "file_type", "comments", "plain_text", "xmp", "icc", "animation", "app_extensions",
        "trailing", "file_system", "hashes"]
    assert field(data, "comments", "comment_1") == "Made by Jane Doe"
    assert field(data, "plain_text", "plain_text_1") == "hello"
    assert field(data, "animation", "frames") == 2
    assert field(data, "animation", "loop_count") == "forever"
    critical = [w for w in data["warnings"] if w.startswith("CRITICAL")]
    assert len(critical) == 2  # XMP GPS, data after the trailer


def test_show_gif_structure(tmp_path):
    p = tmp_path / "anim.gif"
    p.write_bytes(_gif_with_metadata())
    result = run_show(str(p), "--structure")
    assert result.returncode == 0, result.stderr
    assert "GIF structure" in result.stdout and "NETSCAPE2.0" in result.stdout


def test_show_malformed_gif_is_partly_examined(tmp_path):
    p = tmp_path / "broken.gif"
    p.write_bytes(b"GIF89a" + bytes(20))  # a zero byte where a block should start
    data = show_json(p)
    assert field(data, "file_type", "embedded_metadata") == "partly examined (file is malformed)"
    assert any("malformed GIF" in w for w in data["warnings"])


def test_show_gif_with_broken_header(tmp_path):
    p = tmp_path / "broken.gif"
    p.write_bytes(b"GIF89a\x01")
    data = show_json(p)
    assert field(data, "file_type", "embedded_metadata") == "not examined (file is malformed)"


# -- WebP ----------------------------------------------------------------------------------


def _webp_with_metadata():
    from helpers.webp import anim, anmf, chunk, exif_chunk, extended, iccp, vp8l, xmp_chunk
    from helpers.jpeg import SAMPLE_XMP, icc_profile
    from helpers.tiff import ASCII, RATIONAL, SHORT, build_tiff

    tiff = build_tiff([(0x010F, ASCII, "ExampleCam"), (0x0112, SHORT, 6)],
                      gps=[(0x0001, ASCII, "N"), (0x0002, RATIONAL, [(48, 1), (51, 1), (0, 1)]),
                           (0x0003, ASCII, "E"), (0x0004, RATIONAL, [(2, 1), (17, 1), (0, 1)])])
    return extended(iccp(icc_profile()), anim(), anmf(chunk("VP8L", vp8l(4, 4))),
                    anmf(chunk("VP8L", vp8l(4, 4, green2=3))), exif_chunk(tiff),
                    xmp_chunk(SAMPLE_XMP), chunk("abcd", b"private"),
                    trailing=b"after the RIFF data")


def test_show_webp_sections(tmp_path):
    p = tmp_path / "image.webp"
    p.write_bytes(_webp_with_metadata())
    data = show_json(p)
    assert field(data, "file_type", "name") == "WebP"
    assert field(data, "file_type", "embedded_metadata") == "examined"
    assert field(data, "file_type", "support") == "supported"
    assert list(data["sections"]) == [
        "file_type", "exif", "gps", "xmp", "icc", "animation", "unknown", "trailing",
        "file_system", "hashes"]
    assert field(data, "exif", "Make") == "ExampleCam"
    assert field(data, "animation", "frames") == 2
    critical = [w for w in data["warnings"] if w.startswith("CRITICAL")]
    assert len(critical) == 3  # EXIF GPS, XMP GPS, data after the RIFF end


def test_show_webp_structure(tmp_path):
    p = tmp_path / "image.webp"
    p.write_bytes(_webp_with_metadata())
    result = run_show(str(p), "--structure")
    assert result.returncode == 0, result.stderr
    assert "WebP structure" in result.stdout and "ANMF" in result.stdout


def test_show_malformed_webp_is_partly_examined(tmp_path):
    p = tmp_path / "broken.webp"
    p.write_bytes(_webp_with_metadata()[:60])
    data = show_json(p)
    assert field(data, "file_type", "embedded_metadata") == "partly examined (file is malformed)"
    assert any("malformed WebP" in w for w in data["warnings"])


# -- SVG -----------------------------------------------------------------------------------


def test_show_svg_sections(tmp_path):
    from helpers.svg import inkscape_svg

    p = tmp_path / "plan.svg"
    p.write_bytes(inkscape_svg())
    data = show_json(p)
    assert field(data, "file_type", "name") == "SVG"
    assert field(data, "file_type", "embedded_metadata") == "examined"
    assert field(data, "file_type", "support") == "viewing supported; stripping not implemented yet"
    keys = list(data["sections"])
    assert keys[:7] == ["file_type", "svg_metadata", "title_desc", "comments", "editor",
                        "local_paths", "embedded_images"]
    assert {"exif", "gps", "xmp"} <= set(keys)
    assert data["sections"]["gps"]["title"] == "Embedded image 1 (JPEG): GPS"
    editor = {f["label"]: f["value"] for f in data["sections"]["editor"]["fields"].values()}
    assert editor["sodipodi:docname"] == "floor plan.svg"
    critical = [w for w in data["warnings"] if w.startswith("CRITICAL")]
    assert critical[0].startswith("CRITICAL: 2 attribute(s) contain absolute local file paths")


def test_show_svg_structure(tmp_path):
    from helpers.svg import illustrator_svg

    p = tmp_path / "logo.svg"
    p.write_bytes(illustrator_svg())
    result = run_show(str(p), "--structure")
    assert result.returncode == 0, result.stderr
    assert "SVG structure" in result.stdout and "xmlns:i" in result.stdout


def test_show_svg_with_doctype_is_refused(tmp_path):
    p = tmp_path / "old.svg"
    p.write_bytes(b'<?xml version="1.0"?>\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
                  b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
                  b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    data = show_json(p)
    assert field(data, "file_type", "embedded_metadata") == "not examined (file is malformed)"
    assert any("DOCTYPE or ENTITY" in w for w in data["warnings"])
