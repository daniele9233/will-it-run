"""The pictures and PDFs the suite builds itself: deterministic, readable, and with a truth known exactly."""

from __future__ import annotations

import base64
import struct
import zlib

import pytest

from agentfit.arena import pdfgen
from agentfit.arena.pngart import Canvas


def png_pixels(data: bytes) -> tuple[int, int, bytes]:
    """Decode the PNGs the canvas writes (8-bit RGB, no interlace) with nothing but zlib, to check them independently."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, width, height = 8, b"", 0, 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        assert zlib.crc32(kind + body) == crc, kind
        if kind == b"IHDR":
            width, height, depth, color, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
            assert (depth, color, interlace) == (8, 2, 0)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 3 + 1
    rows = []
    for y in range(height):
        assert raw[y * stride] == 0  # filter type 0: the rows are stored as they are
        rows.append(raw[y * stride + 1 : (y + 1) * stride])
    return width, height, b"".join(rows)


def pixel(pixels: bytes, width: int, x: int, y: int) -> tuple[int, int, int]:
    i = (y * width + x) * 3
    return pixels[i], pixels[i + 1], pixels[i + 2]


def test_a_canvas_writes_a_valid_png_with_the_pixels_that_were_drawn():
    canvas = Canvas(40, 30, background=(255, 255, 255))
    canvas.rect(5, 5, 10, 10, (255, 0, 0))
    canvas.circle(30, 15, 6, (0, 0, 255))
    width, height, pixels = png_pixels(canvas.png())
    assert (width, height) == (40, 30)
    assert pixel(pixels, width, 8, 8) == (255, 0, 0)
    assert pixel(pixels, width, 30, 15) == (0, 0, 255)
    assert pixel(pixels, width, 0, 0) == (255, 255, 255)
    assert pixel(pixels, width, 22, 15) == (255, 255, 255)


def test_the_pixel_hash_depends_on_the_picture_and_not_on_how_it_was_compressed():
    a, b = Canvas(20, 20, background=(0, 0, 0)), Canvas(20, 20, background=(0, 0, 0))
    a.rect(2, 2, 5, 5, (9, 9, 9))
    b.rect(2, 2, 5, 5, (9, 9, 9))
    assert a.pixels_sha256() == b.pixels_sha256()
    b.rect(10, 10, 2, 2, (1, 2, 3))
    assert a.pixels_sha256() != b.pixels_sha256()


def test_text_is_drawn_with_a_built_in_font_and_measures_what_it_draws():
    canvas = Canvas(120, 30, background=(255, 255, 255))
    assert canvas.text_width("TOTALE 42", scale=2) == 9 * 6 * 2 - 2
    canvas.text(4, 4, "TOTALE 42", (0, 0, 0), scale=2)
    _w, _h, pixels = png_pixels(canvas.png())
    dark = sum(1 for i in range(0, len(pixels), 3) if pixels[i] == 0)
    assert dark > 100
    blank = Canvas(120, 30, background=(255, 255, 255))
    assert blank.pixels_sha256() != canvas.pixels_sha256()
    with pytest.raises(ValueError):
        canvas.text(0, 0, "€", (0, 0, 0))  # a glyph the font does not have is an error, not a silent gap
    with pytest.raises(ValueError):
        canvas.text(0, 0, "lowercase", (0, 0, 0))


def test_two_canvases_drawn_the_same_way_give_byte_identical_png_files():
    def draw() -> bytes:
        canvas = Canvas(64, 48, background=(240, 240, 240))
        canvas.rect(4, 4, 20, 20, (200, 30, 30))
        canvas.text(30, 10, "AB12", (0, 0, 0))
        return canvas.png()

    assert draw() == draw()
    assert base64.b64decode(base64.b64encode(draw())) == draw()


# -- PDF ----------------------------------------------------------------------------------------------


def test_a_generated_pdf_reads_back_as_the_text_it_was_made_from():
    pages = [["FATTURA N. 2026/118", "Cliente: Rossi Srl", "Totale: 1.220,00 EUR"], ["Condizioni", "Pagamento a 30 giorni (art. 5)"]]
    data = pdfgen.build(pages, title="Fattura")
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    assert pdfgen.page_count(data) == 2
    assert pdfgen.extract_pages(data) == pages
    assert pdfgen.extract_text(data) == "\n".join(pages[0]) + "\n\f\n" + "\n".join(pages[1])


def test_special_characters_survive_the_round_trip():
    pages = [["Prezzo: 12,50 € — IVA (22%) inclusa", "Città: Perugia; Università: è già", r"Path C:\temp\file e \ backslash"]]
    assert pdfgen.extract_pages(pdfgen.build(pages)) == pages


def test_the_same_pages_always_give_the_same_bytes_and_so_the_same_hash():
    pages = [["una riga", "un'altra riga"]]
    assert pdfgen.build(pages, title="x") == pdfgen.build(pages, title="x")


def test_a_table_is_laid_out_in_columns_and_reads_back_one_row_per_line():
    rows = [["Articolo", "Qtà", "Totale"], ["Tastiera", "4", "100,00"], ["Monitor", "2", "300,00"]]
    data = pdfgen.build([pdfgen.table_lines(rows)])
    (page,) = pdfgen.extract_pages(data)
    assert page[0].split() == ["Articolo", "Qtà", "Totale"]
    assert page[2].split() == ["Monitor", "2", "300,00"]


def test_extraction_handles_a_compressed_stream_too():
    # A PDF made elsewhere usually compresses its content streams: the reader has to cope.
    plain = pdfgen.build([["Ciao dal mondo"]])
    content = b"BT /F1 12 Tf 72 700 Td (Ciao dal mondo) Tj ET"
    packed = zlib.compress(content)
    compressed = plain.replace(content, packed).replace(b"/Length %d" % len(content), b"/Length %d /Filter /FlateDecode" % len(packed))
    assert pdfgen.extract_pages(compressed) == [["Ciao dal mondo"]]


def test_garbage_is_not_a_pdf():
    with pytest.raises(ValueError):
        pdfgen.extract_pages(b"this is not a pdf")
