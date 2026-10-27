# Phase 5: Custom evaluation cases, final run and reports

## What
- **7 custom cases** in `evaluation/custom_cases/`. Each is a self-contained mini dataset: the same CSVs and `evidence/` layout as `dataset/`, plus `expected_output.csv` and a README.
  - Built by `scripts/make_custom_cases.py`.
  - Scored by `scripts/run_custom_cases.py` (writes `results.md`).
  - What they test: sync recreating data, restore from backup, email changed before the request, a non-deletion request, a tombstone leaking an email hash, a prompt injection in evidence, and evidence exported after `audit_at`.
- **`build_context(root=...)`:** the audit core runs against any directory with the dataset layout.
- **LLM intent fallback, now wired:** only AMBIGUOUS texts reach it; cached and ledgered; on failure, or with no LLM configured, the request is NOT_APPLICABLE.
- **Run log** (`data/run_log.jsonl`): one record per pipeline run, with LLM calls and tokens (from the ledger), OCR cache hits and computed runs, seconds, and the output sha256.
- **Report generators:**
  - `scripts/usage_report.py`: per run, plus the final run and the cold work, labelled separately;
  - `scripts/evaluation_report.py`: everything recomputed from measurements.
- **Final `output.csv`** (518 rows), `evaluation/evaluation_report.md` and `evaluation/usage_report.md`.

## Why
The brief requires at least 3 custom cases, plus both reports built from the final run. Running the custom cases through the same code path, as mini datasets, tests the real pipeline rather than a mock of it.

## How
- **Custom expected answers** were written by hand from the brief's rules before the pipeline ran on them.
- **Final run, done twice by the developer.** Determinism is measured by the sha256 of the two outputs, not assumed.
- **The validator gates submission.** It caught real defects on the first final run (below).

## Decisions & tradeoffs
- **c7 test-design fix.** The first c7 had no data linking the account to the subject, and the pipeline correctly did not guess. The workflow activity that every real request has was added; the expected answer was unchanged. This is recorded as failure #9, not hidden.
- **C16, from validator errors on the first final run:**
  - replica `customer_data_*` snapshots cover the profile store even with no profile row (subject-scoped). Errors 35 → 2.
  - account tie-breaks prefer an account read back in the case's own eligible files. Errors 2 → 0; ties 121 → 114; sample F1 95.7% → 96.5%.
- **Cost reporting.** The usage report gives $0 with no hosted-API comparison, because no verified price was cited.

## Results
| Item | Value |
|---|---|
| Final run | `20261024T175512Z-d09b69`: 518 rows (162 COMPLETE / 346 INCOMPLETE / 10 NOT_APPLICABLE), 3,004 steps, 6.6 s |
| Determinism | repeat run byte-identical, sha256 `65fadb7ddb560c011a233e0295438e00603b3b51c1f38b74e2482b5541f951c0` |
| Validator | 518 rows, 0 errors, 1 warning (case_61: no evidence at or before `audit_at` mentions the subject) |
| LLM / OCR | 0 calls, 0 tokens, $0; OCR 11/11 cache hits |
| Samples | status 100%, step F1 96.5% (P 99.5%, R 93.8%), exact sequence 65.6% |
| Custom cases | 7/7 (status, exact sequence, validator) |

## How to verify it yourself (Git Bash)
```bash
cd ~/Downloads/hackerrank-orchestrate-october26
.venv/Scripts/python.exe -m pytest -q                                            # 82 passed, 1 skipped
.venv/Scripts/python.exe scripts/make_custom_cases.py && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/run_custom_cases.py
RUN_LABEL=final PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m app.pipeline.run_all
.venv/Scripts/python.exe -m app.eval.validate_output                             # 0 errors
sha256sum output.csv                                                             # 65fadb7d...
.venv/Scripts/python.exe scripts/usage_report.py
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/evaluation_report.py
```

## Known limitations
- **Account ties:** 114 eval cases rely on a tie-broken account attribution. This affects only the account VERIFY step's `resource_id`, never the status.
- **case_61** is COMPLETE with `[]`, because no eligible evidence exists for that subject.
- **Unreachable sample expectations:** 3 (see `docs/conventions.md`).
