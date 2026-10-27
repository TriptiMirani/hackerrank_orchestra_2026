"""Score predicted rows against expected rows (sample_output.csv or custom cases).

Metrics (all computed, never estimated):
  * status accuracy        : deletion_status equal, per case;
  * step-level P/R/F1      : steps as (phase, system_id, resource_id, operation, state, evidence_id) multisets;
  * field-level accuracy   : expected steps aligned to predicted ones (same phase+operation+resource, then same
                             phase+operation in order); per field, the share of expected steps whose aligned
                             prediction has the right value (unaligned = wrong for every field);
  * sequence accuracy      : the whole steps array identical (incl. step_no), per case; plus mean normalized
                             edit similarity of the step sequences, so near misses are visible.
Usage: python -m app.eval.scorer --pred evaluation/sample_predictions.csv [--expected dataset/sample_output.csv]
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from app.config import DATASET_DIR

FIELDS = ["step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"]
STEP_KEY = FIELDS[1:]


def read_rows(path: Path) -> dict[str, tuple[str, list[dict]]]:
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(fh)}


def _key(s: dict) -> tuple:
    return tuple(s[k] for k in STEP_KEY)


def align(expected: list[dict], predicted: list[dict]) -> list[tuple[dict, dict | None]]:
    remaining = list(predicted)
    pairs: list[tuple[dict, dict | None]] = []
    pending = []
    for e in expected:
        m = next((p for p in remaining if (p["phase"], p["operation"], p["resource_id"]) ==
                  (e["phase"], e["operation"], e["resource_id"])), None)
        if m is not None:
            remaining.remove(m)
            pairs.append((e, m))
        else:
            pending.append(e)
    for e in pending:
        m = next((p for p in remaining if (p["phase"], p["operation"]) == (e["phase"], e["operation"])), None)
        if m is not None:
            remaining.remove(m)
        pairs.append((e, m))
    return pairs


def edit_similarity(a: list[tuple], b: list[tuple]) -> float:
    if not a and not b:
        return 1.0
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return 1 - prev[-1] / max(len(a), len(b))


def score(expected: dict, predicted: dict) -> dict:
    cases = sorted(expected)
    status_ok = tp = n_pred = n_exp = seq_exact = 0
    field_ok = Counter()
    sims = []
    per_case = []
    for cid in cases:
        e_status, e_steps = expected[cid]
        p_status, p_steps = predicted.get(cid, ("<missing>", []))
        status_ok += e_status == p_status
        ce, cp = Counter(map(_key, e_steps)), Counter(map(_key, p_steps))
        hit = sum((ce & cp).values())
        tp += hit
        n_exp += len(e_steps)
        n_pred += len(p_steps)
        exact = e_steps == p_steps
        seq_exact += exact
        sims.append(edit_similarity([_key(s) for s in e_steps], [_key(s) for s in p_steps]))
        for e, p in align(e_steps, p_steps):
            for f in FIELDS:
                field_ok[f] += p is not None and p[f] == e[f]
        per_case.append({"case_id": cid, "expected": e_status, "predicted": p_status, "exp_steps": len(e_steps),
                         "pred_steps": len(p_steps), "matched_steps": hit, "exact_sequence": exact})
    precision = tp / n_pred if n_pred else 1.0
    recall = tp / n_exp if n_exp else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "cases": len(cases), "status_accuracy": status_ok / len(cases),
        "step_precision": precision, "step_recall": recall, "step_f1": f1,
        "steps_expected": n_exp, "steps_predicted": n_pred, "steps_matched": tp,
        "field_accuracy": {f: field_ok[f] / n_exp if n_exp else 1.0 for f in FIELDS},
        "sequence_exact": seq_exact / len(cases), "sequence_similarity": sum(sims) / len(sims),
        "per_case": per_case,
    }


def render(s: dict) -> str:
    lines = ["| metric | value |", "|---|---|",
             f"| cases | {s['cases']} |",
             f"| status accuracy | {s['status_accuracy']:.1%} |",
             f"| step precision | {s['step_precision']:.1%} ({s['steps_matched']}/{s['steps_predicted']}) |",
             f"| step recall | {s['step_recall']:.1%} ({s['steps_matched']}/{s['steps_expected']}) |",
             f"| step F1 | {s['step_f1']:.1%} |",
             f"| sequence exact match | {s['sequence_exact']:.1%} |",
             f"| sequence edit similarity | {s['sequence_similarity']:.1%} |"]
    lines += ["", "| field | accuracy |", "|---|---|"]
    lines += [f"| {f} | {v:.1%} |" for f, v in s["field_accuracy"].items()]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", type=Path, required=True)
    ap.add_argument("--expected", type=Path, default=DATASET_DIR / "sample_output.csv")
    ap.add_argument("--cases", action="store_true", help="also print the per-case table")
    args = ap.parse_args()
    s = score(read_rows(args.expected), read_rows(args.pred))
    print(render(s))
    if args.cases:
        print("\n| case | expected | predicted | steps exp/pred/matched | exact |\n|---|---|---|---|---|")
        for c in s["per_case"]:
            print(f"| {c['case_id']} | {c['expected']} | {c['predicted']} | {c['exp_steps']}/{c['pred_steps']}/"
                  f"{c['matched_steps']} | {'yes' if c['exact_sequence'] else 'no'} |")


if __name__ == "__main__":
    main()
