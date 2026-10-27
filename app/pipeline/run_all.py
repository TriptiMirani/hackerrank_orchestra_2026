"""Run the audit over a request set and write case_id,deletion_status,steps_json.

  python -m app.pipeline.run_all                       # 518 eval requests -> output.csv (repo root)
  python -m app.pipeline.run_all --split sample --out evaluation/sample_predictions.csv

Deterministic and offline: OCR comes from data/ocr_cache, no LLM call is made (intent is decided by rules for every
request in this dataset; an AMBIGUOUS request would be reported, see run summary). Rows are in natural case order
(case_33 < case_100), which is the order of dataset/deletion_requests.csv.
"""

from __future__ import annotations

import argparse
import sys
import csv
import time
from collections import Counter
from pathlib import Path

import hashlib
import json
import os
from datetime import datetime, timezone

from app.audit.intent import Intent, classify_intent
from app.audit.planner import plan_steps, to_steps_json
from app.audit.status import CaseAssessment, all_requests, assess_case, build_context
from app.config import DATA_DIR, LLM_CACHE_DIR, LLM_LEDGER_PATH, REPO_ROOT
from app.ingest import evidence_index
from app.ingest.loaders import load_requests, load_sample_requests
from app.eval.validate_output import natural_key
from app.llm.ledger import Ledger, new_run_id, read_ledger

RUN_LOG = DATA_DIR / "run_log.jsonl"


def intent_extractor_factory(ledger: Ledger):
    """Only invoked if some request is AMBIGUOUS. Without a usable .env/Ollama it returns None, and the case
    becomes NOT_APPLICABLE (an unclear request authorizes no deletion)."""

    def factory():
        try:
            from app.config import get_settings
            from app.llm.cache import LLMCache
            from app.llm.client import build_extractor

            return build_extractor(get_settings(), ledger, LLMCache(LLM_CACHE_DIR))
        except Exception as exc:  # missing .env keys, refused config, no ollama package
            print(
                f"[intent] LLM unavailable ({type(exc).__name__}); ambiguous requests -> NOT_APPLICABLE"
            )
            return None

    return factory


def display_path(path: Path) -> str:
    """Repo-relative when inside the repo (so ./evaluation/x.csv works), absolute otherwise."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def case_sort_key(case_id: str) -> tuple[int, str]:
    return natural_key(case_id)   # one definition, shared with the validator


def run(
    split: str, run_id: str | None = None
) -> tuple[list[tuple[str, str, str]], dict]:
    started = time.monotonic()
    ledger = Ledger(LLM_LEDGER_PATH, run_id=run_id)
    ctx = build_context(all_requests())
    ctx.intent_extractor_factory = intent_extractor_factory(ledger)
    reqs = load_requests() if split == "eval" else load_sample_requests()
    rows, stats = [], Counter()
    # Hard rule 9: list unreadable/partially extracted files and every case for which such a file was eligible
    # evidence (captured <= audit_at). Their content is unknown, so dependence cannot be narrowed further.
    failed = {fid: r.error or r.status.value for fid, r in ctx.index.results.items()
              if r.status.value in ("PARSE_FAILED", "NEEDS_OCR")}
    review: dict[str, list[str]] = {"identity_unresolved": [], "case_errors": [],
                                    "depends_on_failed_extraction": [], "intent_fallback": [], "unresolved_records": []}
    for req in sorted(reqs, key=lambda r: case_sort_key(r.case_id)):
        try:
            a = assess_case(req, ctx)
        except Exception as exc:  # one bad case must not stop the other 517 from being assessed (hard rule 9)
            review["case_errors"].append(f"{req.case_id}: {type(exc).__name__}: {exc}"[:200])
            a = CaseAssessment(case_id=req.case_id, status="ERROR", intent=classify_intent(req.request_text),
                               notes=["CASE_ERROR"])
        if a.intent.source == "fallback":   # AMBIGUOUS text with no LLM decision (or a failed LLM call)
            review["intent_fallback"].append(req.case_id)
        review["unresolved_records"] += [f"{req.case_id}:{i.resource.resource_id}" for i in a.unresolved]
        if any(n.startswith("IDENTITY_UNRESOLVED") for n in a.notes):
            review["identity_unresolved"].append(req.case_id)
        if failed and any(ctx.index.files[f].captured_at <= req.audit_at for f in failed):
            review["depends_on_failed_extraction"].append(req.case_id)
        steps = plan_steps(a)
        rows.append((req.case_id, a.status, to_steps_json(steps)))
        stats[a.status] += 1
        stats["ambiguous_intent"] += a.rule_intent == Intent.AMBIGUOUS.value  # before the LLM/fallback
        stats["tie_account"] += any("(tie)" in i.link_detail for i in a.items)
        stats["steps"] += len(steps)
        stats["llm_intent"] += a.intent.source == "llm"
    stats["seconds"] = round(time.monotonic() - started, 1)
    stats["run_id"] = ledger.run_id
    stats["failed_extractions"] = failed
    stats["review"] = review
    return rows, dict(stats)


def write_csv(rows, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["case_id", "deletion_status", "steps_json"])
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["eval", "sample"], default="eval")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out = args.out or (
        REPO_ROOT / "output.csv"
        if args.split == "eval"
        else REPO_ROOT / "evaluation" / "sample_predictions.csv"
    )
    run_id = new_run_id()
    rows, stats = run(args.split, run_id)
    blocked = stats["review"]["case_errors"] + stats["review"]["identity_unresolved"]
    if blocked:
        # Fail loudly instead of writing a row that claims COMPLETE/INCOMPLETE without proof.
        print(f"ABORTED: {len(blocked)} case(s) could not be assessed; {out.name} was NOT written.")
        for b in blocked:
            print("  ", b)
        sys.exit(2)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(rows, out)
    calls = [e for e in read_ledger(LLM_LEDGER_PATH) if e.run_id == run_id]
    record = {
        "run_id": run_id,
        "run_label": os.environ.get("RUN_LABEL", "dev"),
        "split": args.split,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": len(rows),
        "statuses": {
            k: stats.get(k, 0) for k in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")
        },
        "steps": stats.get("steps", 0),
        "ambiguous_intent": stats.get("ambiguous_intent", 0),
        "llm_calls": sum(not e.cached and not e.summary for e in calls),
        "llm_cache_hits": sum(e.cached for e in calls),
        "llm_tokens_in": sum(e.prompt_eval_count for e in calls),
        "llm_tokens_out": sum(e.eval_count for e in calls),
        "ocr": dict(evidence_index.OCR_STATS),
        "tie_account_cases": stats.get("tie_account", 0),
        "failed_extractions": stats["failed_extractions"],
        "cases_identity_unresolved": stats["review"]["identity_unresolved"],
        "case_errors": stats["review"]["case_errors"],
        "cases_depending_on_failed_extractions": stats["review"]["depends_on_failed_extraction"],
        "cases_intent_fallback": stats["review"]["intent_fallback"],
        "unresolved_records": stats["review"]["unresolved_records"],
        "seconds": stats["seconds"],
        "output": display_path(out),
        "output_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
    }
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    print(f"wrote {len(rows)} rows -> {out}")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
