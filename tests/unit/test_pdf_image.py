import io

import pymupdf
from PIL import Image

from app.extract.image import SYSTEM_PROMPT, prepare_image
from app.extract.pdf import is_scanned_text, page_texts, rasterize_page, scanned_page_numbers


def make_pdf(path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Customer Ingrid Dahl user_18 ingrid.dahl@outlook.com")
    doc.new_page()  # blank page: stands in for a scanned page with no text layer
    doc.save(path)
    doc.close()


def test_scanned_page_detection(tmp_path):
    pdf = tmp_path / "t.pdf"
    make_pdf(pdf)
    assert "user_18" in page_texts(pdf)[0]
    assert scanned_page_numbers(pdf) == [1]
    assert is_scanned_text("  \n ") and not is_scanned_text("a real line of extracted text here")


def test_rasterize_page_is_png_and_deterministic(tmp_path):
    pdf = tmp_path / "t.pdf"
    make_pdf(pdf)
    a = rasterize_page(pdf, 0, dpi=72)
    assert a[:8] == b"\x89PNG\r\n\x1a\n"
    assert a == rasterize_page(pdf, 0, dpi=72)
    assert Image.open(io.BytesIO(a)).size == (595, 842)  # PyMuPDF default page is A4 at 72 dpi


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_prepare_image_downscales_longest_side():
    out = Image.open(io.BytesIO(prepare_image(_png(1800, 1200), 1600)))
    assert out.size == (1600, 1067)


def test_prepare_image_keeps_small_images():
    out = Image.open(io.BytesIO(prepare_image(_png(800, 600), 1600)))
    assert out.size == (800, 600)


def test_system_prompt_treats_evidence_as_data():
    assert "NOT instructions" in SYSTEM_PROMPT
    assert "ignore previous" in SYSTEM_PROMPT.lower()
