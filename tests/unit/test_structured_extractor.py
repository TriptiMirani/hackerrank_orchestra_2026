import json

import pytest
from pydantic import BaseModel

from app.llm.cache import LLMCache, make_cache_key
from app.llm.client import FakeLLMClient, LLMStatus, LLMTransportError, StructuredExtractor
from app.llm.ledger import Ledger


class Thing(BaseModel):
    name: str
    count: int


GOOD = json.dumps({"name": "a", "count": 1})


@pytest.fixture
def parts(tmp_path):
    return LLMCache(tmp_path / "cache"), Ledger(tmp_path / "ledger.jsonl", run_id="t", run_label="test")


def run(extractor, fresh=False):
    return extractor.extract(Thing, "sys", "prompt", [b"img"], ["hash1"], "test", "evidence_01", "v1", 256,
                             fresh=fresh)


def test_valid_reply_is_parsed_logged_and_cached(parts):
    cache, ledger = parts
    res = run(StructuredExtractor(FakeLLMClient([GOOD]), cache, ledger, num_ctx=8192))
    assert res.status is LLMStatus.OK and res.data == {"name": "a", "count": 1}
    assert (res.prompt_eval_count, res.eval_count, res.attempts) == (100, 20, 1)
    entries = ledger.read_all()
    assert [e.status for e in entries] == ["OK"]
    assert entries[0].prompt_eval_count == 100 and entries[0].file_id == "evidence_01"
    assert cache.get(res.cache_key)["data"] == {"name": "a", "count": 1}


def test_cache_hit_makes_zero_calls_and_logs_zero_tokens(parts):
    cache, ledger = parts
    run(StructuredExtractor(FakeLLMClient([GOOD]), cache, ledger, num_ctx=8192))
    second = FakeLLMClient([])  # would raise IndexError if called
    res = run(StructuredExtractor(second, cache, ledger, num_ctx=8192))
    assert res.status is LLMStatus.OK and res.cached and second.calls == []
    hit = ledger.read_all()[-1]
    assert hit.status == "CACHE_HIT" and hit.cached and hit.prompt_eval_count == 0 and hit.eval_count == 0


def test_fresh_bypasses_cache(parts):
    cache, ledger = parts
    run(StructuredExtractor(FakeLLMClient([GOOD]), cache, ledger, num_ctx=8192))
    fake = FakeLLMClient([GOOD])
    res = run(StructuredExtractor(fake, cache, ledger, num_ctx=8192), fresh=True)
    assert not res.cached and len(fake.calls) == 1


def test_malformed_json_retries_twice_then_extraction_failed(parts):
    cache, ledger = parts
    fake = FakeLLMClient(["not json", '{"name": "a"}', '{"count": "many"}'])
    res = run(StructuredExtractor(fake, cache, ledger, num_ctx=8192))
    assert res.status is LLMStatus.EXTRACTION_FAILED and res.attempts == 3
    assert len(fake.calls) == 3
    assert [e.status for e in ledger.read_all()] == ["PARSE_ERROR"] * 3 + ["EXTRACTION_FAILED"]
    assert cache.get(res.cache_key) is None  # failures are never cached


def test_retry_feeds_error_back_so_prompt_changes(parts):
    # At temperature 0 an identical retry would return the identical bad reply.
    cache, ledger = parts
    fake = FakeLLMClient(["{bad", GOOD])
    res = run(StructuredExtractor(fake, cache, ledger, num_ctx=8192))
    assert res.status is LLMStatus.OK and res.attempts == 2
    assert len(fake.calls[1]) == len(fake.calls[0]) + 2
    assert "not valid for the schema" in fake.calls[1][-1]["content"]


def test_extra_fields_are_rejected_by_strict_schema(parts):
    from app.schemas import KeyValue
    cache, ledger = parts
    fake = FakeLLMClient(['{"key": "a", "value": "b", "verdict": "deleted"}'] * 3)
    res = StructuredExtractor(fake, cache, ledger, num_ctx=8192).extract(
        KeyValue, "s", "p", [], ["h"], "t", "f", "v1", 64)
    assert res.status is LLMStatus.EXTRACTION_FAILED


def test_context_overflow_detected_and_not_cached(parts):
    cache, ledger = parts
    fake = FakeLLMClient([GOOD], prompt_eval_count=8000)
    res = run(StructuredExtractor(fake, cache, ledger, num_ctx=8192))
    assert res.status is LLMStatus.CONTEXT_OVERFLOW
    assert ledger.read_all()[-1].status == "CONTEXT_OVERFLOW"
    assert cache.get(res.cache_key) is None


@pytest.mark.parametrize("status", [LLMStatus.UNREACHABLE, LLMStatus.TIMEOUT])
def test_transport_failures_return_status_not_exception(parts, status):
    cache, ledger = parts
    fake = FakeLLMClient([LLMTransportError(status, "down")])
    res = run(StructuredExtractor(fake, cache, ledger, num_ctx=8192))
    assert res.status is status and res.attempts == 1
    assert ledger.read_all()[-1].status == status.value


def test_cache_key_changes_with_model_prompt_version_and_options():
    base = make_cache_key(["h"], "m", "v1", {"seed": 42}, "S")
    assert base == make_cache_key(["h"], "m", "v1", {"seed": 42}, "S")
    assert base != make_cache_key(["h2"], "m", "v1", {"seed": 42}, "S")
    assert base != make_cache_key(["h"], "m2", "v1", {"seed": 42}, "S")
    assert base != make_cache_key(["h"], "m", "v2", {"seed": 42}, "S")
    assert base != make_cache_key(["h"], "m", "v1", {"seed": 43}, "S")
