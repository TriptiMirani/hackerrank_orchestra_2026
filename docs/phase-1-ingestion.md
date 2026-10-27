# Phase 1: Typed ingestion and deterministic evidence parsing

## What
- **Typed CSV loaders** (`app/ingest/loaders.py`): strict Pydantic models with literal enums for all 9 CSVs, plus a column-order check.
- **One evidence vocabulary** (`app/schemas.py`):
  - `Observation`: file F saw resource R in system S at time T, present / absent / unknown, with identifiers, attributes and the reader's lookup key.
  - `ScopeCoverage`: file F lists the whole collection C at time T, so a resource missing from it is absent.
  - `TextLine`: free text for identifier matching.
  - `ParseResult`: one per file, with status `OK`, `PARSE_FAILED` or `NEEDS_OCR`.
- **Parsers** (`app/extract/`):
  - `sqlsnap.py`: inventory, replica-topology and typed tables;
  - `jsonrec.py`: 4 document shapes;
  - `logparse.py`: 7 line formats, plus trace assembly;
  - `textrec.py`: handover file;
  - `pdf.py`: text lines;
  - `normalize.py` / `lookup.py`: shared field vocabulary;
  - `router.py`: dispatch;
  - `evidence_index.py`: parse once, index by resource.
- **Proof tooling** (`scripts/parse_report.py`): facts per case, the sample-citation check, and the per-file summary table.
- **Conventions** (`docs/conventions.md`): C1–C10, each checked against the samples.

## Why
Every decision in the samples has the form "resource R was in state X at or before `audit_at`, according to file F". Normalizing all 94 files into that one shape now means Phases 3–4 work on one type instead of 94 file formats. It also means every fact carries its `file_id` and locator, so citations come for free.

## How
- **Dispatch on content shape, never on file name:**
  - SQL: `snapshot_scope` table, a `record_json` column, or a typed table;
  - JSON: `service_exports`, `snapshots`, `collection`, or lookup ledger.
- **Field vocabulary:** source field names map onto one vocabulary (`requester_email`/`owner_email`/`customer_email` → `email`, etc.).
  - `null` and `None` strings become None.
  - An identifier field that exists but is null is kept in `attrs`, which is how a redacted field is detected.
- **Logs:** each line is normalized to (ts, service, trace_id, event, fields). A completed event inherits the key from its trace's started event (`lookup_key`, `match_key`, `subject_key`, `owner_key`, `subject_ref`).
- **Presence from logs:**
  - explicit signals: `http_status`, `rows`, `hit`/`miss`, `available`, `archive_present`;
  - otherwise, a read or scan that returned content means present;
  - otherwise unknown.
- **Absence:** the parsers never infer absence. They emit `ScopeCoverage` only where a source enumerates a collection: `snapshot_scope` systems, bundle snapshots, topology tables, single-collection exports.

## Decisions & tradeoffs
- **Query keys are not stored data.** `correlation_key` and the other query keys go to `lookup_key`, never to `identifiers`, because a 404 lookup also carries one. Treating them as stored PII would make deleted records look exposed.
- **Citation rule C3.** Only files with manifest `captured_at` ≤ `audit_at` count. Within those, the latest observation is cited. This reproduces 70 of 70 direct sample citations. A filter on inner timestamps alone gets 32 of them wrong (evidence_02, evidence_06).
- **The category comes from the source** (collection, `record_type` or event name) and is never guessed. Support message vs attachment is left as None when the source doesn't say.
- **Unknown service names** (`profile_service`, `support_replica`) map to the system owning the resource-id prefix. Snapshot-bundle store names (`backup_restore_service`, …) are kept as-is for now; this is an open question for Phase 3.
- **Parse in memory, no cache:** all 94 files take about 8 s, so a cache isn't worth the complexity.

## How to verify it yourself (Git Bash)
```bash
cd ~/Downloads/hackerrank-orchestrate-october26
.venv/Scripts/python.exe -m pytest -q                                  # 54 passed, 1 skipped
.venv/Scripts/python.exe scripts/parse_report.py --summary | head -20   # per-file parse status
.venv/Scripts/python.exe scripts/parse_report.py --citations | tail -3  # 70 FOUND + 5 NOT_IN_FILE (absence/PDF)
.venv/Scripts/python.exe scripts/parse_report.py --case case_05 --case case_15 --case case_32
```

## Known limitations
- Support records outside the pii-scan logs, collections and handover file have no message/attachment category.
- The snapshot-bundle system aliases are unresolved.
- PDF and screenshot text become facts only in Phase 2.
- Presence is what the source says at `observed_at`; reconciling conflicting sources is Phase 3.
