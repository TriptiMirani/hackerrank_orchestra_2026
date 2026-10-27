"""Opt-in: RUN_LIVE=1 python -m pytest tests/live -q   (calls local gemma4; get an estimate + OK first)."""
import pytest

from app.config import DATASET_DIR, get_settings
from app.extract.image import extract_screen
from app.llm.cache import LLMCache
from app.llm.client import LLMStatus, build_extractor
from app.llm.ledger import Ledger


@pytest.mark.live
def test_one_screenshot_extracts_and_then_hits_cache(tmp_path):
    settings = get_settings()
    ex = build_extractor(settings, Ledger(tmp_path / "ledger.jsonl", run_label="live-test"),
                         LLMCache(tmp_path / "cache"))
    path = DATASET_DIR / "evidence/images/search_index_inspector_20260901.png"
    first = extract_screen(ex, "evidence_09", path, settings.MAX_IMAGE_SIDE, settings.LLM_NUM_PREDICT)
    assert first.status is LLMStatus.OK and first.prompt_eval_count > 0
    assert "search_428" in str(first.data)
    second = extract_screen(ex, "evidence_09", path, settings.MAX_IMAGE_SIDE, settings.LLM_NUM_PREDICT)
    assert second.cached and second.data == first.data
