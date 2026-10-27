# AI interview notes

## 1. Approach in one paragraph
I built a **deterministic** auditor rather than "an LLM reading files".
- **Parsing:** every one of the 94 evidence files is parsed into a single `Observation` model: file, locator, system, resource, time, present/absent, identifiers. There are 3 SQL table shapes, 4 JSON shapes, 7 log line formats, a handover text file, PDF text and OCR'd screenshots.
- **Per request:** classify intent (rules in 6 languages) → resolve the subject's current and historical identifiers → link resources to the subject → reconstruct each resource's state at `audit_at` → apply `deletion_rules.csv` → derive the status → emit canonically ordered steps.
- **Proof:** the same input always gives byte-identical output, and every finding cites a participant-visible `file_id`.

## 2. Architecture (where to look)
| Area | Code |
|---|---|
| Evidence parsing | `app/extract/` (sqlsnap, jsonrec, logparse, textrec, pdf, ocr, textfacts) |
| Audit core | `app/audit/` (intent, identity, timeline, rules, flows, workflow, status, planner) |
| Pipeline / evaluation | `app/pipeline/run_all.py`; `app/eval/scorer.py`, `validate_output.py` |
| LLM boundary | `app/llm/`: local Ollama only, guards against cloud models, temperature 0, cache, ledger. Used only for ambiguous intent: 0 calls |

## 3. Investigation approach (what made it work)
- **Conventions reverse-engineered from the samples and checked programmatically** (`docs/conventions.md`, C1–C18).
  - **Biggest single insight (C3):** the cutoff is the **file's** capture time. A file exported after `audit_at` never counts, even if its inner timestamps are earlier. This reproduces 70/70 sample citations; an inner-timestamp filter gets 32 wrong.
- **Identity:** `email_hash = sha256(lower(email))`, confirmed 850/850. 100 of 518 requests resolve only through a historical email.
- **Absence needs proof:**
  - a negative readback (404, `rows=0`, `NOT_FOUND`); or
  - **subject-scoped coverage**: a snapshot that enumerates a collection for that subject.
  - Treating per-subject extracts as full enumerations produced 22 wrong verdicts while status still looked 100%. That is why I verify per resource, not only per case.
- **Replication drift:** each physical copy has its own timeline. case_05's account is gone from the primary but still in the failover replica.
- **Already-deleted records have no identifiers left,** so the only link is the deletion workflow's own activity. I attribute it with a time window, agreement between result and reported status, exclusion of resources owned by other people, and constraint propagation. Ties are labelled. This adds +4 F1 points and never changes status.

## 4. Remediation planning
- **Mapping:** each violation maps to finding → remediation → verification:
  - REMAINING → DELETE;
  - EXPOSED → REDACT or ANONYMIZE, by rule;
  - active flow → DISABLE;
  - restore path → BLOCK.
- **Kept records** (audit logs, legal holds, tombstones) are verified PRESENT or REDACTED, never deleted.
- **Ordering:** flows are disabled and restores blocked before records are changed. The order is (phase, operation-table index, system_id, resource_id).
- **Citations:** COMPLETE plans cite the proof of the current state. INCOMPLETE remediation and verification steps are future work with an empty `evidence_id`.

## 5. Evaluation
- **Samples:** status 100%; step F1 96.5% (P 99.5%, R 93.8%); exact sequence 65.6%. All 13 failing steps are classified mechanically:
  - 7 records never linked to a person in any file;
  - 3 expected records not in any participant file;
  - 2 symmetric account ties;
  - 1 activity-only record.
- **Ablations:** without workflow attribution, F1 drops to 91.6%; without late-file retained checks, to 95.1%.
- **7 custom mini-dataset cases,** hand-written expected answers, 7/7 pass: sync recreation, restore risk, historical email, non-deletion, tombstone hash leak, prompt injection, late evidence.
- **Final run:** 518 rows, validator 0 errors, 0 LLM calls, $0, two runs byte-identical.

## 6. Failures I found and fixed (the honest list)
1. **The vision LLM was blind** (0/74, all invented, yet schema-valid) → RapidOCR plus matching against *known* identifiers (74/74). Lesson: schema validation catches malformed output, not hallucination.
2. **OCR quirks:** panel text bleeding into table rows, emails read as names, file names read as resource IDs → region split and tuned regex boundaries.
3. **Replica drift hidden** (case_05) → per-copy timelines.
4. **False absences from per-subject extracts** → subject-scoped coverage.
5. **The validator caught 35 uncited COMPLETE checks** on the first final run → replica snapshots cover the profile store, and tie-breaks prefer grounded evidence.
6. **Two code reviews:**
   - round 1: an unproven "disabled/blocked" when evidence was silent; two image-only cases turned out to show an active flow and an open restore;
   - round 2: never claim COMPLETE without proof, so unassessable cases now stop the run; strict name linking.
   - Each fix was measured separately (`docs/code-review-2026-10-24.md`).
7. **The LLM intent fallback was documented but not wired** → wired, cached, and its decisions persisted.

## 7. Tradeoffs and limits
- **114 eval cases use a tie-broken account ID.** It affects one verification step, never status.
- **Unreachable sample expectations:** some expected resources exist in no participant file. I chose not to guess them.
- **Intent rules are tuned to this dataset's phrasing.** The LLM fallback covers unseen phrasings.
- **The local LLM was nearly unused by design.** The "AI" is in the investigation strategy and in OCR; every decision is code, which makes it auditable and reproducible.
