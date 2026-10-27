# Phase 3: Audit core

## What
Phase 3 lives in `app/audit/`.
- **`intent.py`:** decides whether `request_text` authorizes deletion. Rules in 6 languages:
  - *strong* negation or limitation markers always win;
  - *weak* other-right markers count only when there is no deletion phrase;
  - otherwise the request is `AMBIGUOUS`, which would go to the LLM. No request does.
- **`identity.py`:** maps the request email (current or historical) to the user, then collects every identifier valid at or before `audit_at`, including derived `sha256(lower(email))` hashes.
- **`timeline.py`:** resource state at `audit_at`. It applies:
  - C3 file eligibility;
  - a separate timeline per physical copy (live system and each replica);
  - absence by coverage, scoped to the subject when the source is an extract.
- **`rules.py` / `flows.py`:** the policy treatment per category, forbidden identifiers (a tombstone keeps `user_ref`; analytics may not), active data flows and open restore paths.
- **`workflow.py`:** attributes `deletion_worker` activity to requests. It uses a time window, agreement between result and reported status, ownership exclusion and constraint propagation, with a confidence on every link.
- **`status.py`:**
  - links resources to the subject: strong, then page, then workflow, plus late retained;
  - gives each resource a verdict: violation (REMAINING, EXPOSED, ACTIVE_FLOW, RESTORE_RISK) or compliant (the matching `*_OK`);
  - selects the primary account;
  - sets the status.
- **`scripts/status_check.py`:** status table and per-case dumps. **`tests/unit/test_audit.py`:** 12 tests.

## Why
`deletion_status` and every later step are derived from these verdicts. Code decides all of them; no LLM is involved (hard rule 6).

## How
1. Intent. `NOT_DELETION` → `NOT_APPLICABLE`.
2. Resolve the identity.
3. Link resources to the subject:
   - **strong:** an identifier or lookup key in an eligible file, or a catalog link activity;
   - **page:** a weak OCR page subject;
   - **workflow:** attributed workflow activity.
4. For each linked resource, compute its state at `audit_at`, resolve its category (from the source; prefix fallback; no rule → DELETE), and apply the treatment to get a verdict.
5. Keep resources whose state is known, plus one primary account.
6. INCOMPLETE if any violation, otherwise COMPLETE.
7. INCOMPLETE plans also get retained records seen only in late files, as uncited future checks (C13).

## Decisions & tradeoffs
- **Status cannot depend on a guess.** Violations always carry identifiers and so are strongly linked. The inferred workflow links only ever add already-compliant records.
- **Ownership versus state.** Ownership ("which person a resource belongs to") may use all files, because it is static. State and citations never use a file exported after `audit_at`.
- **Account ties.** These are broken deterministically (closest to `reported_at`, then resource ID) and labelled `tie`: 121 of 508 eval deletion cases use one. No ties are broken for non-account records, since an unforced guess could hand one person's record to another.
- **Rejected alternatives:**
  - one timeline per resource: hid replica drift (case_05);
  - treating every snapshot as a full enumeration: false absences (case_24);
  - a wide request window: up to 27 overlapping cases.

## Failures fixed (sample per-resource verdicts)
| Step | Status | Correct | Wrong | Missing |
|---|---|---|---|---|
| First run | 31/32 | 71 | 22 | 18 |
| Per-copy replica timelines | 32/32 | 71 | 22 | 18 |
| Subject-scoped coverage, coverage for activity-only resources, drop state-less items, one primary account | 32/32 | 93 | 0 | 18 |
| Late retained future checks (C13) | 32/32 | 98 | 0 | 13 |

## How to verify it yourself (Git Bash)
```bash
cd ~/Downloads/hackerrank-orchestrate-october26
.venv/Scripts/python.exe -m pytest -q                                          # 73 passed, 1 skipped
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/status_check.py       # 32/32
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/status_check.py --show case_05 case_29 case_31
```

## Known limitations
- **Unreachable expectations:** `audit_record_6645`, `audit_record_5569` and `account_7012` are not in participant-visible evidence.
- **Symmetric attribution:** 8 sample analytics profiles anonymized before any evidence saw them.
- **Intent:** the rules are tuned to this dataset's phrasing. A new phrasing that matches nothing becomes `AMBIGUOUS` and would need the LLM fallback, which is not exercised by this dataset.
