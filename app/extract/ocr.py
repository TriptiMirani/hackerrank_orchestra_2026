"""Screenshot evidence -> text lines with boxes, via RapidOCR (deterministic, CPU, offline).

Replaces the LLM for images: gemma4's vision is broken in this install (PROJECT_STATE.md,
2026-10-24 vision probe). OCR only transcribes; deciding which identifiers matter is done later
by matching KNOWN identifiers (from user_identities + audit log) against these lines.
Results are cached like LLM results: key = sha256(file bytes) + engine + OCR_VERSION.
"""
from __future__ import annotations

import time
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from app.llm.cache import LLMCache, make_cache_key, sha256_bytes

OCR_ENGINE = "rapidocr-onnxruntime==1.4.4"
# Bump when the engine, its parameters, or the post-processing below changes.
OCR_VERSION = "ocr-v1"


class OcrStatus(str, Enum):
    OK = "OK"
    OCR_FAILED = "OCR_FAILED"


class OcrLine(BaseModel):
    text: str
    score: float
    x0: int
    y0: int
    x1: int
    y1: int


class OcrResult(BaseModel):
    file_id: str
    status: OcrStatus
    engine: str = OCR_ENGINE
    lines: list[OcrLine] = []
    seconds: float = 0.0
    cached: bool = False
    error: str | None = None

    def texts(self) -> list[str]:
        return [line.text for line in self.lines]


# The engine is a callable(bytes) -> (raw_result, elapsed); injectable so unit tests stay fast.
OcrEngine = Callable[[bytes], Any]
_ENGINE: OcrEngine | None = None


def default_engine() -> OcrEngine:
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR  # imported lazily: model load takes ~1-2 s
        _ENGINE = RapidOCR()
    return _ENGINE


def parse_raw(raw: Any) -> list[OcrLine]:
    """RapidOCR returns [[box(4 points), text, score], ...] or None when no text is found."""
    lines = []
    for box, text, score in raw or []:
        xs = [int(round(p[0])) for p in box]
        ys = [int(round(p[1])) for p in box]
        lines.append(OcrLine(text=str(text), score=round(float(score), 4),
                             x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys)))
    return lines


def ocr_bytes(file_id: str, data: bytes, cache: LLMCache, engine: OcrEngine | None = None,
              fresh: bool = False) -> OcrResult:
    key = make_cache_key([sha256_bytes(data)], OCR_ENGINE, OCR_VERSION, {}, "OcrResult")
    if not fresh:
        hit = cache.get(key)
        if hit is not None:
            # A hit costs 0 s; the original OCR time stays in the cache file for reference.
            return OcrResult.model_validate({**hit["data"], "file_id": file_id, "cached": True, "seconds": 0.0})

    started = time.monotonic()
    try:
        raw, _elapsed = (engine or default_engine())(data)
    except Exception as exc:  # unreadable/corrupt image: a state, not a crash (hard rule 9)
        return OcrResult(file_id=file_id, status=OcrStatus.OCR_FAILED, error=f"{type(exc).__name__}: {exc}",
                         seconds=round(time.monotonic() - started, 3))
    result = OcrResult(file_id=file_id, status=OcrStatus.OK, lines=parse_raw(raw),
                       seconds=round(time.monotonic() - started, 3))
    cache.put(key, {"data": result.model_dump(mode="json", exclude={"cached"}), "ocr_version": OCR_VERSION})
    return result


def ocr_file(file_id: str, path: Path, cache: LLMCache, engine: OcrEngine | None = None,
             fresh: bool = False) -> OcrResult:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        return OcrResult(file_id=file_id, status=OcrStatus.OCR_FAILED, error=f"unreadable: {exc}")
    return ocr_bytes(file_id, data, cache, engine, fresh)
