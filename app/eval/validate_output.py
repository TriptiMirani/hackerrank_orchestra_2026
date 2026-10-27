"""Validate an output CSV against the submission contract (AGENTS.md section 6.3/6.4, docs/conventions.md C1/C2).

Errors make the file invalid; warnings are legal but suspicious (e.g. a COMPLETE case with no provable step).
Usage: python -m app.eval.validate_output [--file output.csv] [--requests dataset/deletion_requests.csv]
Exit code 0 = valid.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from app.audit.planner import OP_ORDER, PHASES, STATE_OF
from app.config import DATASET_DIR, REPO_ROOT

HEADER = ["case_id", "deletion_status", "steps_json"]
STEP_FIELDS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}


def natural_key(case_id: str) -> tuple[int, str]:
    tail = case_id.rsplit("_", 1)[-1]
    return (int(tail), case_id) if tail.isdigit() else (sys.maxsize, case_id)


def validate_rows(header: list[str], rows: list[dict], expected_ids: list[str]) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    if header != HEADER:
        errors.append(f"header {header} != {HEADER}")
    ids = [r.get("case_id", "") for r in rows]
    if len(rows) != len(expected_ids):
        errors.append(f"row count {len(rows)} != {len(expected_ids)}")
    if len(set(ids)) != len(ids):
        errors.append("duplicate case_id rows")
    if set(ids) != set(expected_ids):
        errors.append(f"case_ids differ: missing={sorted(set(expected_ids) - set(ids))[:5]} "
                      f"extra={sorted(set(ids) - set(expected_ids))[:5]}")
    if ids != sorted(ids, key=natural_key):
        errors.append("rows not sorted by case_id (natural order, as in deletion_requests.csv)")
    for r in rows:
        cid, status = r.get("case_id", "?"), r.get("deletion_status", "")
        if status not in STATUSES:
            errors.append(f"{cid}: bad deletion_status {status!r}")
            continue
        raw = r.get("steps_json")
        if raw is None:  # short CSV row: csv.DictReader fills missing columns with None
            errors.append(f"{cid}: missing steps_json column")
            continue
        try:
            steps = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"{cid}: steps_json is not valid JSON ({exc.msg})")
            continue
        if not isinstance(steps, list):
            errors.append(f"{cid}: steps_json is not a JSON array")
            continue
        errors += [f"{cid}: {e}" for e in check_steps(status, steps)]
        if status == "COMPLETE" and not steps:
            warnings.append(f"{cid}: COMPLETE with no verification step (nothing provable was linked)")
    return errors, warnings


def check_steps(status: str, steps: list) -> list[str]:
    errs = []
    if status == "NOT_APPLICABLE":
        return [] if steps == [] else ["NOT_APPLICABLE must have steps_json []"]
    last = (-1, -1)
    for i, s in enumerate(steps, start=1):
        if not isinstance(s, dict) or set(s) != STEP_FIELDS:
            errs.append(f"step {i}: fields must be exactly {sorted(STEP_FIELDS)}")
            continue
        if s["step_no"] != i:
            errs.append(f"step {i}: step_no {s['step_no']} (must be consecutive from 1)")
        if s["phase"] not in PHASES or s["operation"] not in OP_ORDER.get(s["phase"], []):
            errs.append(f"step {i}: operation {s['operation']} not allowed in phase {s['phase']}")
            continue
        if STATE_OF[s["operation"]] != s["state"]:
            errs.append(f"step {i}: illegal operation/state pair {s['operation']}/{s['state']}")
        order = (PHASES.index(s["phase"]), OP_ORDER[s["phase"]].index(s["operation"]))
        if order < last:
            errs.append(f"step {i}: out of canonical phase/operation order")
        last = order
        if status == "COMPLETE":
            if s["phase"] != "VERIFICATION":
                errs.append(f"step {i}: COMPLETE may only contain VERIFICATION steps")
            if not s["evidence_id"]:
                errs.append(f"step {i}: COMPLETE verification must cite evidence")
        elif status == "INCOMPLETE":
            if s["phase"] == "FINDING" and not s["evidence_id"]:
                errs.append(f"step {i}: finding must cite evidence")
            if s["phase"] != "FINDING" and s["evidence_id"]:
                errs.append(f"step {i}: future {s['phase'].lower()} step must have empty evidence_id")
    if status == "INCOMPLETE" and not any(s.get("phase") == "FINDING" for s in steps if isinstance(s, dict)):
        errs.append("INCOMPLETE must report at least one finding")
    return errs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path, default=REPO_ROOT / "output.csv")
    ap.add_argument("--requests", type=Path, default=DATASET_DIR / "deletion_requests.csv")
    args = ap.parse_args()
    with args.requests.open(encoding="utf-8", newline="") as fh:
        expected_ids = [r["case_id"] for r in csv.DictReader(fh)]
    with args.file.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        header = reader.fieldnames or []
    errors, warnings = validate_rows(header, rows, expected_ids)
    print(f"{args.file}: {len(rows)} rows, {len(errors)} errors, {len(warnings)} warnings")
    for e in errors[:50]:
        print("ERROR  ", e)
    for w in warnings[:50]:
        print("WARNING", w)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
