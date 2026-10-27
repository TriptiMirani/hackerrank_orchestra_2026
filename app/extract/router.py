"""Route each manifest entry to its deterministic parser. One bad file returns PARSE_FAILED, never raises."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.config import DATASET_DIR
from app.extract.lexicon import Lexicon
from app.extract.ocr import OcrResult, OcrStatus
from app.extract.textfacts import group_observations, ocr_groups, page_groups
from app.extract.jsonrec import parse_json
from app.extract.logparse import parse_log
from app.extract.pdf import is_scanned_text, page_texts
from app.extract.sqlsnap import parse_sql
from app.extract.textrec import parse_text
from app.schemas import EvidenceFile, ParseResult, ParseStatus, TextLine

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
OcrFn = Callable[[str, Path], OcrResult]


def parse_evidence(ef: EvidenceFile, root: Path = DATASET_DIR, lexicon: Lexicon | None = None,
                   ocr: OcrFn | None = None) -> ParseResult:
    """lexicon/ocr are optional so Phase 1 parsing works alone; without them, PDFs give lines only and images
    stay NEEDS_OCR."""
    path = root / ef.file_path
    ext = path.suffix.lower()
    base = dict(file_id=ef.file_id, file_path=ef.file_path)
    try:
        if ext == ".sql":
            obs, cov = parse_sql(ef.file_id, path, ef.captured_at)
            return ParseResult(**base, parser="sqlsnap", status=ParseStatus.OK, observations=obs, coverage=cov)
        if ext == ".json":
            obs, cov = parse_json(ef.file_id, path, ef.captured_at)
            return ParseResult(**base, parser="jsonrec", status=ParseStatus.OK, observations=obs, coverage=cov)
        if ext == ".log":
            obs, _events = parse_log(ef.file_id, path)
            return ParseResult(**base, parser="logparse", status=ParseStatus.OK, observations=obs)
        if ext == ".txt":
            obs, lines = parse_text(ef.file_id, path)
            return ParseResult(**base, parser="textrec", status=ParseStatus.OK, observations=obs, lines=lines)
        if ext == ".pdf":
            return _pdf(ef, path, base, lexicon)
        if ext in IMAGE_EXT:
            return _image(ef, path, base, lexicon, ocr)
        return ParseResult(**base, parser="none", status=ParseStatus.PARSE_FAILED, error=f"no parser for '{ext}'")
    except Exception as exc:  # hard rule 9: a state, not a crash
        return ParseResult(**base, parser=ext.lstrip("."), status=ParseStatus.PARSE_FAILED,
                           error=f"{type(exc).__name__}: {exc}")


def _image(ef: EvidenceFile, path: Path, base: dict, lexicon: Lexicon | None, ocr: OcrFn | None) -> ParseResult:
    if ocr is None or lexicon is None:
        return ParseResult(**base, parser="ocr", status=ParseStatus.NEEDS_OCR)
    res = ocr(ef.file_id, path)
    if res.status is not OcrStatus.OK:
        return ParseResult(**base, parser="ocr", status=ParseStatus.PARSE_FAILED, error=res.error)
    lines = [TextLine(file_id=ef.file_id, locator=f"ocr ({l.x0},{l.y0})-({l.x1},{l.y1}) score={l.score}",
                      text=l.text) for l in res.lines]
    obs = group_observations(ef.file_id, "ocr", ef.captured_at, ocr_groups(res.lines), lexicon, ef.origin)
    return ParseResult(**base, parser="ocr", status=ParseStatus.OK, observations=obs, lines=lines)


def _pdf(ef: EvidenceFile, path: Path, base: dict, lexicon: Lexicon | None = None) -> ParseResult:
    lines, scanned = [], []
    for p_no, text in enumerate(page_texts(path), start=1):
        if is_scanned_text(text):
            scanned.append(p_no)
            continue
        for l_no, line in enumerate(text.splitlines(), start=1):
            if line.strip():
                lines.append(TextLine(file_id=ef.file_id, locator=f"page {p_no} line {l_no}", text=line.strip()))
    status = ParseStatus.NEEDS_OCR if scanned else ParseStatus.OK
    obs = []
    if lexicon is not None:
        obs = group_observations(ef.file_id, "pdf", ef.captured_at, page_groups(lines), lexicon, ef.origin)
    return ParseResult(**base, parser="pdf", status=status, lines=lines, observations=obs,
                       error=f"scanned pages need OCR: {scanned}" if scanned else None)
