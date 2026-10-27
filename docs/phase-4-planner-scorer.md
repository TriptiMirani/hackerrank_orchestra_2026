# Phase 4: Step planner, scorer and validator

## What
- **`app/audit/planner.py`** turns a `CaseAssessment` into ordered steps:
  - each violation → finding, remediation, verification;
  - each compliant record → verification only;
  - `NOT_APPLICABLE` → `[]`.
  Output is compact JSON with a fixed key order, so the bytes are stable.
- **`app/pipeline/run_all.py`** writes `output.csv`, or `evaluation/sample_predictions.csv` with `--split sample`. Rows are in natural case order.
- **`app/eval/scorer.py`** computes:
  - status accuracy;
  - step precision, recall and F1 over 6-field steps;
  - per-field accuracy over aligned steps;
  - exact sequence match plus edit similarity.
- **`app/eval/validate_output.py`** checks the contract: header, row count/set/order, JSON, exact step fields, consecutive `step_no`, legal phase/operation/state, canonical order, citation rules per status. A COMPLETE case with no steps is a warning.
- **`scripts/error_analysis.py`** lists every failing sample step with a cause derived from the data, plus ablations.

## Why
- The planner is pure formatting over Phase 3 verdicts, so ordering and citation rules live in one place (`canonical_sort`, `plan_steps`).
- The scorer has to show *where* points are lost: a single tie-broken account ID zeroes a case's exact-sequence match, but costs only one step.

## How
- **Violation mapping:**

  | Verdict | Finding | Remediation | Verification |
  |---|---|---|---|
  | REMAINING | REPORT_REMAINING_RECORD | DELETE_RECORD | VERIFY_RECORD_ABSENT |
  | EXPOSED (REDACT) | REPORT_EXPOSED_PII | REDACT_RECORD | VERIFY_RECORD_REDACTED |
  | EXPOSED (ANONYMIZE) | REPORT_EXPOSED_PII | ANONYMIZE_RECORD | VERIFY_RECORD_ANONYMIZED |
  | ACTIVE_FLOW | REPORT_ACTIVE_DATA_FLOW | DISABLE_DATA_FLOW | VERIFY_DATA_FLOW_DISABLED |
  | RESTORE_RISK | REPORT_RESTORE_RISK | BLOCK_RESTORE | VERIFY_RESTORE_BLOCKED |

- **Compliant verdicts** map to their check. An UNKNOWN primary account becomes `VERIFY_RECORD_ABSENT`.
- **One sort key** gives the whole order: (phase, operation-table index, system_id, resource_id). `step_no` is assigned after sorting.
- **Citations:**
  - COMPLETE: every step cites the file that proves the current state;
  - INCOMPLETE: only findings cite; remediation and future verification steps get `""`.
- **Scorer alignment:** an expected step is matched first by (phase, operation, resource); failing that, by (phase, operation) in order. An unaligned expected step counts as wrong in every field.

## Decisions & tradeoffs
- **Natural case order** (`case_33 … case_550`) matches `deletion_requests.csv`. The developer accepted this.
- **Remediation order:** flows are disabled and restores blocked before records are changed, which the operation-table order already guarantees. Ties are ordered by `system_id` as the samples show. Downstream-before-source ordering is therefore not applied: case_16 deletes the account before the search entry.
- **Calibration:** the validator and scorer are checked against the ground truth, which passes with 0 errors and scores 100%.
- **Tried and rejected:** pre-filling workflow slots. No measured change on 550 requests, so it was reverted.

## Results (samples)
| Variant | Status | Step F1 | Exact sequences |
|---|---|---|---|
| final | 100% | 95.7% | 65.6% |
| without workflow attribution | 100% | 91.6% | 31.2% |
| without late-file retained checks | 100% | 94.3% | 56.2% |

All 16 failing steps have a cause: 7 never-identified, 6 tie-attribution, 3 not-in-participant-data, 0 other.

## How to verify it yourself (Git Bash)
```bash
cd ~/Downloads/hackerrank-orchestrate-october26
.venv/Scripts/python.exe -m pytest -q                                              # 80 passed, 1 skipped
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m app.pipeline.run_all --split sample
.venv/Scripts/python.exe -m app.eval.scorer --pred evaluation/sample_predictions.csv --cases
.venv/Scripts/python.exe -m app.eval.validate_output --file evaluation/sample_predictions.csv --requests dataset/sample_deletion_requests.csv
.venv/Scripts/python.exe -m app.eval.validate_output --file dataset/sample_output.csv --requests dataset/sample_deletion_requests.csv
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/error_analysis.py
```

## Known limitations
- case_04 gets COMPLETE with `[]`: neither of its expected resources is identifiable from participant data.
- Exact-sequence accuracy is bounded by tie-broken account IDs (121 of 508 eval deletion cases use one).
