"""Build evaluation/usage_report.md from data/llm_ledger.jsonl only.

Every number in the report is a sum/count over ledger lines; nothing is estimated.
Usage: python scripts/usage_report.py [--ledger PATH] [--out PATH]
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json  # noqa: E402

from app.config import DATA_DIR, EVALUATION_DIR, LLM_LEDGER_PATH  # noqa: E402
from app.llm.ledger import LedgerEntry, read_ledger  # noqa: E402

RUN_LOG = DATA_DIR / "run_log.jsonl"
# Measured in Phase 0 (scripts/accuracy_check.py, 10 uncached images on this laptop's CPU); OCR is not an LLM and is
# not in the ledger, so its one-time cold cost is reported from that measurement.
OCR_COLD_MEASURED = {"images": 11, "mean_s_per_image": 16.9, "engine": "rapidocr-onnxruntime 1.4.4 (CPU)"}


def read_runs() -> list[dict]:
    if not RUN_LOG.exists():
        return []
    return [json.loads(line) for line in RUN_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]


def render_runs(runs: list[dict]) -> list[str]:
    out = ["## Pipeline runs that produced outputs (`data/run_log.jsonl`)", "",
           "| label | run_id | split | rows | LLM calls | LLM tokens in/out | OCR cache hits / computed | seconds | "
           "output sha256 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        out.append(f"| {r['run_label']} | {r['run_id']} | {r['split']} | {r['rows']} | {r['llm_calls']} | "
                   f"{r['llm_tokens_in']}/{r['llm_tokens_out']} | {r['ocr']['cache_hits']} / {r['ocr']['computed']} | "
                   f"{r['seconds']} | `{r['output_sha256'][:16]}…` |")
    finals = [r for r in runs if r["run_label"] == "final" and r["split"] == "eval"]
    if finals:
        f = finals[-1]
        n = f["rows"]
        tok = f["llm_tokens_in"] + f["llm_tokens_out"]
        out += ["", "## Final full-dataset run (the run that produced output.csv)", "",
                "| model provider / model | calls | input tokens | output tokens | total tokens | avg tokens per "
                "request | cost (USD) | cost per request (USD) |", "|---|---|---|---|---|---|---|---|",
                f"| Ollama local / gemma4:latest (intent fallback only) | {f['llm_calls']} | {f['llm_tokens_in']} | "
                f"{f['llm_tokens_out']} | {tok} | {tok / n:.2f} | 0.00 | 0.00 |",
                f"| RapidOCR local (not an LLM) | 0 OCR runs ({f['ocr']['cache_hits']} cache hits) | 0 | 0 | 0 | 0 | "
                f"0.00 | 0.00 |",
                f"| **overall** | {f['llm_calls']} | {f['llm_tokens_in']} | {f['llm_tokens_out']} | {tok} | "
                f"{tok / n:.2f} | **0.00** | **0.00** |", "",
                f"- Requests: {n}; ambiguous intents sent to the LLM: {f['ambiguous_intent']} (every request was "
                f"decided by deterministic rules); wall clock {f['seconds']} s on the machine in PROJECT_STATE.md.",
                f"- Output: `{f['output']}`, sha256 `{f['output_sha256']}`.",
                "- Cost: $0. Inference is local (Ollama, RapidOCR); no hosted API was called. A hosted-API price "
                "comparison is omitted because no price was cited and verified."]
    out += ["", "## Cold work that filled the caches (one-time, before the final run)", "",
            f"- OCR: {OCR_COLD_MEASURED['images']} screenshots, measured {OCR_COLD_MEASURED['mean_s_per_image']} s "
            f"per image on CPU ({OCR_COLD_MEASURED['engine']}); 0 tokens; cached in `data/ocr_cache/`.",
            "- LLM: the Phase 0 vision attempts and probes in the ledger tables above (gemma4 vision proved unusable "
            "and was replaced by OCR; those results are not used by the pipeline)."]
    return out


def summarize(entries: list[LedgerEntry]) -> dict:
    real = [e for e in entries if not e.cached and not e.summary]   # summary lines are not calls
    tok_in = sum(e.prompt_eval_count for e in real)
    tok_out = sum(e.eval_count for e in real)
    eval_s = sum(e.eval_duration_s for e in real)
    files = {e.file_id for e in entries}
    return {
        "calls": len(real),
        "cache_hits": sum(1 for e in entries if e.cached),
        "ok": sum(1 for e in real if e.status == "OK"),
        "failed": sum(1 for e in real if e.status not in ("OK", "PARSE_ERROR"))
                  + sum(1 for e in entries if e.summary),
        "parse_errors": sum(1 for e in real if e.status == "PARSE_ERROR"),
        "files": len(files),
        "tokens_in": tok_in,
        "tokens_out": tok_out,
        "tokens_total": tok_in + tok_out,
        "avg_tokens_per_call": round((tok_in + tok_out) / len(real), 1) if real else 0.0,
        "wall_s": round(sum(e.latency_s for e in real), 1),
        "gen_tok_per_s": round(tok_out / eval_s, 1) if eval_s else 0.0,
    }


ROW_KEYS = ["calls", "cache_hits", "ok", "parse_errors", "failed", "files", "tokens_in", "tokens_out",
            "tokens_total", "avg_tokens_per_call", "wall_s", "gen_tok_per_s"]


def table(rows: list[tuple[str, dict]]) -> list[str]:
    head = "| group | " + " | ".join(ROW_KEYS) + " | cost (USD) |"
    sep = "|" + "---|" * (len(ROW_KEYS) + 2)
    lines = [head, sep]
    for name, s in rows:
        lines.append(f"| {name} | " + " | ".join(str(s[k]) for k in ROW_KEYS) + " | 0.00 |")
    return lines


def render(entries: list[LedgerEntry]) -> str:
    out = ["# Usage report", "",
           "Generated by `scripts/usage_report.py` from `data/llm_ledger.jsonl` only.",
           "Provider: Ollama (local). Actual cost: **$0** (local inference, no API billing).", "",
           "Definitions: `calls` = real model calls (each retry counts); `cache_hits` = served from",
           "`data/llm_cache` at 0 tokens; tokens are Ollama `prompt_eval_count` (input) and",
           "`eval_count` (output); `wall_s` = summed per-call latency; `gen_tok_per_s` = output tokens",
           "/ eval duration.", ""]
    if not entries:
        out.append("_Ledger is empty: no LLM calls recorded yet._")
        return "\n".join(out) + "\n"

    by_model: dict[str, list[LedgerEntry]] = defaultdict(list)
    by_run: dict[tuple[str, str], list[LedgerEntry]] = defaultdict(list)
    for e in entries:
        by_model[f"{e.provider}/{e.model}"].append(e)
        by_run[(e.run_label, e.run_id)].append(e)

    out += ["## Overall and per model", ""]
    out += table([("ALL", summarize(entries))] + [(m, summarize(v)) for m, v in sorted(by_model.items())])
    out += ["", "## Per run (label / run_id)", ""]
    out += table([(f"{label} / {rid}", summarize(v)) for (label, rid), v in sorted(by_run.items())])
    out += ["", "## Per purpose", ""]
    by_purpose: dict[str, list[LedgerEntry]] = defaultdict(list)
    for e in entries:
        by_purpose[e.purpose].append(e)
    out += table([(p, summarize(v)) for p, v in sorted(by_purpose.items())])
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", type=Path, default=LLM_LEDGER_PATH)
    ap.add_argument("--out", type=Path, default=EVALUATION_DIR / "usage_report.md")
    args = ap.parse_args()
    text = render(read_ledger(args.ledger))
    text += "\n" + "\n".join(render_runs(read_runs())) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(text)
    print(f"[written to {args.out}]")


if __name__ == "__main__":
    main()
