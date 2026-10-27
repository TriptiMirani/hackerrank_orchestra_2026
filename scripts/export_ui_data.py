"""Export every case's assessment for the Gradio case viewer -> deploy/hf_space/cases.json.

Runs the SAME audit code as the final run (offline, cached OCR, no LLM) and stores, per case: the request, the
resolved identifiers, each linked resource's state at audit_at with its evidence, the verdict, the steps, and (for
samples) the expected answer. The Space only reads this file, so it needs no dataset, model, or secret.

Usage: python scripts/export_ui_data.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audit.planner import plan_steps  # noqa: E402
from app.audit.status import all_requests, assess_case, build_context  # noqa: E402
from app.config import DATA_DIR, DATASET_DIR, EVALUATION_DIR, REPO_ROOT  # noqa: E402
from app.eval.scorer import read_rows, score  # noqa: E402
from app.ingest.loaders import load_requests, load_sample_output, load_sample_requests  # noqa: E402

OUT = REPO_ROOT / "deploy" / "hf_space" / "cases.json"


def item_row(it, files) -> dict:
    r = it.resource
    return {"resource_id": r.resource_id, "system_id": r.system_id, "category": it.category,
            "treatment": it.treatment, "verdict": it.verdict.value, "state": r.state,
            "as_of": r.as_of.isoformat().replace("+00:00", "Z") if r.as_of else "",
            "evidence_id": r.evidence_id, "evidence_file": files[r.evidence_id].file_path if r.evidence_id in files else "",
            "basis": r.basis, "link": it.link, "link_detail": it.link_detail, "exposed": it.exposed}


def main() -> None:
    ctx = build_context(all_requests())
    files = ctx.index.files
    expected = {o.case_id: (o.deletion_status, json.loads(o.steps_json)) for o in load_sample_output()}
    sample_ids = {r.case_id for r in load_sample_requests()}
    cases = []
    for req in list(load_requests()) + list(load_sample_requests()):
        a = assess_case(req, ctx)
        i = a.identity
        cases.append({
            "case_id": req.case_id, "split": "sample" if req.case_id in sample_ids else "eval",
            "request": {"email": req.email, "requested_at": req.requested_at.isoformat(),
                        "reported_status": req.reported_status, "reported_at": req.reported_at.isoformat(),
                        "audit_at": req.audit_at.isoformat(), "text": req.request_text},
            "status": a.status, "intent": a.intent.intent.value, "intent_reason": a.intent.reason,
            "identity": None if i is None else {"user_id": i.user_id, "matched_via": i.matched_via,
                                                "emails": i.emails, "phones": i.phones, "names": i.names,
                                                "email_hashes": [h[:16] + "…" for h in i.email_hashes]},
            "items": [item_row(it, files) for it in a.items],
            "unresolved": [it.resource.resource_id for it in a.unresolved],
            "steps": plan_steps(a),
            "expected": None if req.case_id not in expected else
                        {"status": expected[req.case_id][0], "steps": expected[req.case_id][1]},
        })
    sample_score = score(read_rows(DATASET_DIR / "sample_output.csv"), read_rows(EVALUATION_DIR / "sample_predictions.csv"))
    runs = [json.loads(line) for line in (DATA_DIR / "run_log.jsonl").read_text(encoding="utf-8").splitlines() if line]
    final = [r for r in runs if r["run_label"] == "final" and r["split"] == "eval"][-1]
    custom = (EVALUATION_DIR / "custom_cases" / "results.md").read_text(encoding="utf-8")
    payload = {
        "summary": {
            "sample": {k: sample_score[k] for k in ("status_accuracy", "step_precision", "step_recall", "step_f1",
                                                     "sequence_exact", "sequence_similarity")},
            "sample_field_accuracy": sample_score["field_accuracy"],
            "final_run": {k: final[k] for k in ("run_id", "rows", "statuses", "steps", "llm_calls", "seconds",
                                                "output_sha256", "tie_account_cases")},
            "custom_cases_pass": next((l for l in custom.splitlines() if "custom cases pass" in l), ""),
            "output_sha256_now": hashlib.sha256((REPO_ROOT / "output.csv").read_bytes()).hexdigest(),
        },
        "cases": sorted(cases, key=lambda c: (c["split"] != "eval", int(c["case_id"].split("_")[1]))),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    # The exported steps must equal output.csv row for row (the viewer shows exactly what was submitted).
    out_rows = {r["case_id"]: json.loads(r["steps_json"]) for r in csv.DictReader(open(REPO_ROOT / "output.csv",
                                                                                         encoding="utf-8"))}
    mismatch = [c["case_id"] for c in cases if c["split"] == "eval" and c["steps"] != out_rows[c["case_id"]]]
    print(f"wrote {OUT.relative_to(REPO_ROOT)}: {len(cases)} cases, {OUT.stat().st_size / 1024:.0f} KiB; "
          f"eval steps identical to output.csv: {not mismatch} {mismatch[:5]}")


if __name__ == "__main__":
    main()
