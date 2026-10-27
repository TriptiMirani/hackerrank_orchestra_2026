"""Phase 4 proof: every failing sample step with a mechanically derived cause, plus ablations.

  python scripts/error_analysis.py            # writes evaluation/phase4_error_analysis.md and prints it

Causes are derived from the data, not written by hand:
  * not-in-participant-data : the resource appears in no evidence file and no activity row;
  * activity-only           : known only from system_activity (no evidence of state, no identifier);
  * never-identified        : evidence exists but no file ever links it to a person (e.g. anonymized before
                              any capture); only the workflow could link it and its events are ambiguous;
  * tie-attribution         : our account for the case came from a deterministic tie-break;
  * other                   : anything else (would indicate a real bug).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.audit.status as status_mod  # noqa: E402
from app.audit.planner import plan_steps  # noqa: E402
from app.config import EVALUATION_DIR  # noqa: E402
from app.eval.scorer import render, score  # noqa: E402
from app.ingest.loaders import load_activity, load_sample_output, load_sample_requests  # noqa: E402

KEY = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")


def predict(ctx) -> dict:
    return {r.case_id: (a.status, plan_steps(a)) for r in load_sample_requests()
            for a in [status_mod.assess_case(r, ctx)]}


def main() -> None:
    expected = {o.case_id: (o.deletion_status, json.loads(o.steps_json)) for o in load_sample_output()}
    ctx = status_mod.build_context(status_mod.all_requests())
    full = predict(ctx)
    activity_ids = {a.resource_id for a in load_activity()}
    lines = ["# Phase 4 error analysis (samples)", "", "## Score (final)", "", render(score(expected, full)), ""]

    lines += ["## Every failing step", "", "| case | side | step | cause |", "|---|---|---|---|"]
    causes = Counter()
    for cid in sorted(expected, key=lambda c: int(c.split("_")[1])):
        e = [tuple(s[k] for k in KEY) for s in expected[cid][1]]
        p = [tuple(s[k] for k in KEY) for s in full[cid][1]]
        a = status_mod.assess_case(next(r for r in load_sample_requests() if r.case_id == cid), ctx)
        tie = any("(tie)" in i.link_detail for i in a.items)
        missing, extra = Counter(e) - Counter(p), Counter(p) - Counter(e)
        for side, bag in (("missing", missing), ("extra", extra)):
            for step in bag.elements():
                rid = step[2]
                if rid not in ctx.index.by_resource and rid not in activity_ids:
                    cause = "not-in-participant-data"
                elif rid not in ctx.index.by_resource:
                    cause = "tie-attribution" if tie and rid.startswith("account_") else "activity-only"
                elif rid.startswith("account_") and tie:
                    cause = "tie-attribution"
                elif not any(o.identifiers or o.lookup_key for o in ctx.index.by_resource[rid]):
                    cause = "never-identified"
                else:
                    cause = "other"
                causes[cause] += 1
                lines.append(f"| {cid} | {side} | {step[3]} {step[1]}/{rid} [{step[5] or '-'}] | {cause} |")
    lines += ["", "Cause totals: " + ", ".join(f"{k}={v}" for k, v in sorted(causes.items())), ""]

    # Ablations: what each inference mechanism contributes.
    lines += ["## Ablations (before/after)", "", "| variant | status | step P | step R | step F1 | exact seq |",
              "|---|---|---|---|---|---|"]
    variants = {"final": full}
    saved_wf = ctx.workflow
    ctx.workflow = {}
    variants["no workflow attribution"] = predict(ctx)
    ctx.workflow = saved_wf
    saved_late = status_mod._late_retained
    status_mod._late_retained = lambda *a, **k: []
    variants["no late-file retained checks (C13)"] = predict(ctx)
    status_mod._late_retained = saved_late
    for name, pred in variants.items():
        s = score(expected, pred)
        lines.append(f"| {name} | {s['status_accuracy']:.1%} | {s['step_precision']:.1%} | {s['step_recall']:.1%} | "
                     f"{s['step_f1']:.1%} | {s['sequence_exact']:.1%} |")
    text = "\n".join(lines) + "\n"
    out = EVALUATION_DIR / "phase4_error_analysis.md"
    out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
