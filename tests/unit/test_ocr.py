"""OCR wrapper tests with an injected fake engine: offline, fast, deterministic."""
from app.extract.ocr import OcrStatus, ocr_bytes, ocr_file, parse_raw
from app.llm.cache import LLMCache

RAW = [
    [[[277, 407], [347, 407], [347, 422], [277, 422]], "search_428", 0.9991],
    [[[637, 406], [755, 406], [755, 421], [637, 421]], "g.silva76@gmail.com", 0.9963],
]


class FakeEngine:
    def __init__(self, raw=RAW, exc=None):
        self.raw, self.exc, self.calls = raw, exc, 0

    def __call__(self, data):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.raw, [0.1]


def test_parse_raw_boxes_and_text():
    lines = parse_raw(RAW)
    assert [l.text for l in lines] == ["search_428", "g.silva76@gmail.com"]
    assert (lines[0].x0, lines[0].y0, lines[0].x1, lines[0].y1) == (277, 407, 347, 422)
    assert parse_raw(None) == []  # RapidOCR returns None when it finds no text


def test_ocr_result_cached_second_call_skips_engine(tmp_path):
    cache, engine = LLMCache(tmp_path), FakeEngine()
    first = ocr_bytes("evidence_09", b"png-bytes", cache, engine)
    second = ocr_bytes("evidence_09", b"png-bytes", cache, engine)
    assert first.status is OcrStatus.OK and not first.cached
    assert second.cached and second.texts() == first.texts()
    assert engine.calls == 1


def test_fresh_bypasses_ocr_cache(tmp_path):
    cache, engine = LLMCache(tmp_path), FakeEngine()
    ocr_bytes("e", b"x", cache, engine)
    ocr_bytes("e", b"x", cache, engine, fresh=True)
    assert engine.calls == 2


def test_engine_error_is_a_state_and_not_cached(tmp_path):
    cache = LLMCache(tmp_path)
    res = ocr_bytes("e", b"corrupt", cache, FakeEngine(exc=ValueError("bad image")))
    assert res.status is OcrStatus.OCR_FAILED and "bad image" in res.error
    ok_engine = FakeEngine()
    assert ocr_bytes("e", b"corrupt", cache, ok_engine).status is OcrStatus.OK and ok_engine.calls == 1


def test_missing_file_is_a_state(tmp_path):
    res = ocr_file("e", tmp_path / "nope.png", LLMCache(tmp_path / "c"), FakeEngine())
    assert res.status is OcrStatus.OCR_FAILED and res.error.startswith("unreadable")
