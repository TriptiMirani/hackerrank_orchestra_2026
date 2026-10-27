from app.eval.extraction_accuracy import score_extraction
from app.llm.ledger import Ledger, read_ledger
from scripts.usage_report import render, summarize


def test_usage_report_counts_only_real_calls_as_tokens(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl", run_id="r1", run_label="cold")
    common = dict(provider="ollama", model="gemma4:latest", purpose="screen_extraction")
    ledger.append(file_id="e1", status="OK", attempt=1, cached=False, prompt_eval_count=1000,
                  eval_count=200, latency_s=10.0, eval_duration_s=8.0, **common)
    ledger.append(file_id="e1", status="CACHE_HIT", attempt=0, cached=True, **common)
    entries = read_ledger(tmp_path / "l.jsonl")
    s = summarize(entries)
    assert s["calls"] == 1 and s["cache_hits"] == 1
    assert s["tokens_in"] == 1000 and s["tokens_out"] == 200 and s["gen_tok_per_s"] == 25.0
    text = render(entries)
    assert "cold / r1" in text and "ollama/gemma4:latest" in text


def test_usage_report_handles_empty_ledger(tmp_path):
    assert "Ledger is empty" in render(read_ledger(tmp_path / "missing.jsonl"))


def test_accuracy_scoring_found_missed_invented():
    data = {"subtitle": "user_11 · account_5762", "records": [
        {"emails": ["haruka.s1838@zoho.com", "made.up@x.com"], "phones": ["+81-90-7610-2507"]}]}
    expected = {"emails": ["haruka.s1838@zoho.com"], "phones": ["+81 90 7610 2507"],
                "user_ids": ["user_11"], "resource_ids": ["account_5762", "support_9538"]}
    s = score_extraction(data, expected)
    assert s["found"] == 4 and s["expected"] == 5
    assert s["missed"] == ["resource_ids:support_9538"]
    assert s["invented"] == ["made.up@x.com"]
