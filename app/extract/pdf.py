"""PDF text extraction with PyMuPDF; pages without usable text are treated as scanned.

Scanned pages are rasterized at a fixed DPI so the LLM sees the same pixels every run.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

# Fewer non-whitespace characters than this on a page means "no usable text layer".
MIN_TEXT_CHARS = 20


def page_texts(path: Path) -> list[str]:
    with pymupdf.open(path) as doc:
        return [page.get_text() for page in doc]


def is_scanned_text(text: str) -> bool:
    return len("".join(text.split())) < MIN_TEXT_CHARS


def scanned_page_numbers(path: Path) -> list[int]:
    return [i for i, text in enumerate(page_texts(path)) if is_scanned_text(text)]


def rasterize_page(path: Path, page_no: int, dpi: int) -> bytes:
    with pymupdf.open(path) as doc:
        pix = doc[page_no].get_pixmap(dpi=dpi)
        return pix.tobytes("png")
