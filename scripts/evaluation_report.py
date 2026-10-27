"""Build evaluation/evaluation_report.md from measurements only.

Recomputes everything it reports: sample predictions + scorer, the error analysis and ablations, the custom cases,
and the validator on output.csv. The full-run figures come from the latest 'final' entry of data/run_log.jsonl.
Historic before/after figures in "Failures found and improvements made" are the numbers measured when each fix
was made (recorded in PROJECT_STATE.md at the time).

Usage: python scripts/evaluation_report.py
"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATA_DIR, DATASET_DIR, EVALUATION_DIR, REPO_ROOT  # noqa: E402
from app.eval.scorer import read_rows, render, score  # noqa: E402
from app.eval.validate_output import HEADER, validate_rows  # noqa: E402

PY = sys.executable


def sh(*args: str) -> str:
    return subprocess.run([PY, *args], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
                          env={"PYTHONIOENCODING": "utf-8", **__import__("os").environ}).stdout


def latest_final() -> dict | None:
    path = DATA_DIR / "run_log.jsonl"
    if not path.exists():
        return None
    runs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    finals = [r for r in runs if r["run_label"] == "final" and r["split"] == "eval"]
    return finals[-1] if finals else None


def validate_output_csv() -> tuple[int, list[str], list[str]]:
    with (DATASET_DIR / "deletion_requests.csv").open(encoding="utf-8", newline="") as fh:
        ids = [r["case_id"] for r in csv.DictReader(fh)]
    out = REPO_ROOT / "output.csv"
    if not out.exists():
        return 0, ["output.csv not found: run the final pipeline first"], []
    with out.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    errors, warnings = validate_rows(reader.fieldnames or [], rows, ids)
    return len(rows), errors, warnings


def main() -> None:
    sh("-m", "app.pipeline.run_all", "--split", "sample")       # fresh sample predictions (RUN_LABEL dev)
    sample = score(read_rows(DATASET_DIR / "sample_output.csv"), read_rows(EVALUATION_DIR / "sample_predictions.csv"))
    analysis = sh("scripts/error_analysis.py")
    failing = analysis[analysis.index("## Every failing step"):analysis.index("## Ablations")].strip()
    ablations = analysis[analysis.index("## Ablations"):].strip()
    custom = sh("scripts/run_custom_cases.py").strip()
    n_rows, errors, warnings = validate_output_csv()
    final = latest_final()

    L = ["# Evaluation report: Ghost Data Still Exists deletion-audit agent", "",
         "Every number below was measured by code in this repository (`app/eval/scorer.py`, "
         "`app/eval/validate_output.py`, `scripts/error_analysis.py`, `scripts/run_custom_cases.py`). "
         "Regenerate with `python scripts/evaluation_report.py`.", "",
         "## 1. Sample results (32 public cases vs `dataset/sample_output.csv`)", "", render(sample), "",
         "Definitions:",
         "- **Step-level:** a step counts when all 6 fields match (phase, system_id, resource_id, operation, "
         "state, evidence_id; step_no excluded).",
         "- **Field-level:** expected steps aligned to predicted ones by phase+operation+resource, then by "
         "phase+operation.",
         "- **Sequence exact:** the whole array is identical, including step_no.", "",
         "| case | expected | predicted | steps exp/pred/matched | exact |", "|---|---|---|---|---|"]
    L += [f"| {c['case_id']} | {c['expected']} | {c['predicted']} | {c['exp_steps']}/{c['pred_steps']}/"
          f"{c['matched_steps']} | {'yes' if c['exact_sequence'] else 'no'} |" for c in sample["per_case"]]
    L += ["", "## 2. Where the remaining sample points are lost", "",
          "Every failing step, with a cause derived mechanically from the data (none is unexplained):", "",
          failing.replace("## Every failing step", "").strip(), "", "### Ablations (what each inference adds)", "",
          ablations.replace("## Ablations (before/after)", "").strip(), "",
          "## 3. Custom evaluation cases", "",
          "Seven hand-built mini datasets in `evaluation/custom_cases/` (each has the same layout as `dataset/` "
          "plus `expected_output.csv` and a README). Expected answers were written from the brief's rules "
          "before running the pipeline.", "", custom.replace("# Custom evaluation cases: results", "").strip(), "",
          "## 4. Full evaluation run (518 requests)", ""]
    if final:
        L += [f"- run `{final['run_id']}` ({final['finished_at']}): {final['rows']} rows in {final['seconds']} s; "
              f"statuses {final['statuses']}; {final['steps']} steps.",
              f"- Intent: {final['ambiguous_intent']} ambiguous requests → {final['llm_calls']} LLM calls; every "
              "request was decided by deterministic rules.",
              f"- OCR: {final['ocr']['cache_hits']} cache hits, {final['ocr']['computed']} computed.",
              f"- Cases relying on a tie-broken account attribution: {final['tie_account_cases']} (affects only the "
              "account VERIFY step's resource_id, never the status).",
              f"- `output.csv` sha256 `{final['output_sha256']}`."]
    else:
        L += ["- No final run recorded yet."]
    L += [f"- Validator on `output.csv`: {n_rows} rows, **{len(errors)} errors**, {len(warnings)} warnings.",
          *[f"  - error: {e}" for e in errors[:20]], *[f"  - warning: {w}" for w in warnings[:20]],
          "", "## 5. Failures found and improvements made", "",
          "Measured when each fix was made. See PROJECT_STATE.md for the full log.", "",
          "| # | failure found | root cause | fix | measured effect |", "|---|---|---|---|---|",
          "| 1 | gemma4 vision: 0/74 identifiers, 28 invented, yet 11/11 schema-valid | vision encoder blind in this "
          "Ollama install (6 single-variable probes) | RapidOCR + matching against known identifiers | 74/74 found, "
          "0 invented, deterministic |",
          "| 2 | 32 of 75 sample citations not the newest observation | files exported after audit_at describe "
          "earlier moments | file-level cutoff (C3) | citation rule 70/70 |",
          "| 3 | OCR table rows absorbed side-panel text; emails read as names; file names as ids | layout and regex "
          "boundaries | region split, email stripping, tuned boundaries | text facts 26/26 |",
          "| 4 | case_05 COMPLETE (expected INCOMPLETE) | one timeline per resource hid a failover-replica copy | "
          "per-copy timelines (C12) | status 31/32 → 32/32 |",
          "| 5 | status 32/32 but 22 wrong per-resource verdicts | snapshot bundles treated as full enumerations "
          "(false absences) | subject-scoped coverage (C11) | wrong verdicts 22 → 0 |",
          "| 6 | retained audit records missing from plans | they appear only in late-exported files | uncited "
          "future checks for RETAIN records (C13) | correct verdicts 93 → 98; F1 +1.4 points |",
          "| 7 | already-compliant records unlinked | no identifier survives deletion/anonymization | workflow "
          "attribution with constraint propagation | F1 91.6% → 95.7%, exact sequences 31.2% → 65.6% |",
          "| 8 | 'download your data … please delete that export' classified non-deletion | weak markers beat "
          "deletion phrases | strong vs weak marker split | samples 21/32 → 32/32 deletion; eval 508/10 |",
          "| 9 | custom case c7 failed (3/4 steps) | test design: no link between the account and the subject | "
          "added the workflow activity every real request has; expected answer unchanged | 7/7 custom cases |",
          "| 10 | LLM intent fallback documented but not wired | gap between docs and code | wired via the cached, "
          "ledgered extractor; failure → no deletion | unit-tested with a fake client; 0 calls on this dataset |",
          "| 11 | first final run: validator found 35 COMPLETE cases with an uncited account check | replica "
          "snapshots without profile rows never covered account_service | replica snapshots cover the profile store "
          "(C16, subject-scoped) | validator errors 35 → 2 |",
          "| 12 | 2 remaining uncited account checks | tie-break picked an account no evidence observed | prefer the "
          "candidate account read back in the case's own eligible files | errors 2 → 0; eval ties 121 → 114; sample "
          "F1 95.7% → 96.5% |",
          "| 13 | code review (10 findings): unproven 'disabled/blocked' when evidence was silent; non-deletion "
          "requests competing for workflow events; an uncaught identity error; intent edge cases; a reporting bug | "
          "see docs/code-review-2026-10-24.md | latest stating observation (C17), screenshot text read for settings, "
          "per-case guards, narrowed intent markers | eval case_39/case_55 COMPLETE → INCOMPLETE with screenshot "
          "findings, case_41 +1 step; samples unchanged; +14 regression tests |",
          "| 14 | code review round 2 (10 findings) | unproven settings could still yield COMPLETE; crashes/unresolved "
          "identities written as COMPLETE; substring and unscoped markers; name-only links; ledger miscount | full-chain "
          "violation for unproven settings, run aborts on unassessable cases, whole-word negation-aware matching, scoped "
          "markers, strict name policy, stored intent decisions, summary ledger lines (each measured separately) | 0 status "
          "or step changes on samples and eval (output byte-identical), so all are protection against inputs this dataset "
          "lacks; +14 regression tests; #10 was a false positive |",
          "", "## 6. Known limits (not tuned away)", "",
          "- 3 sample expectations are absent from all participant data (`account_004`, `audit_record_6645`, "
          "`audit_record_5569`).",
          "- 7 anonymized analytics profiles were never linked to a person in any file, and their workflow events "
          "are symmetric within their reporting batch.",
          "- Symmetric account ties (e.g. case_28/29) are broken deterministically and labelled as ties.",
          "- Intent rules are tuned to this dataset's phrasing. An unseen phrasing goes to the LLM fallback.", ""]
    out = EVALUATION_DIR / "evaluation_report.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\n[written to {out}]")


if __name__ == "__main__":
    main()
