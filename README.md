# Northstar Deletion Audit Agent: "Ghost Data Still Exists"

A **deterministic** deletion-audit agent. For each of the 518 requests it decides `COMPLETE` / `INCOMPLETE` / `NOT_APPLICABLE`, finds every remaining record, exposed identifier, active data flow and restore path, and emits the ordered `steps_json` plan (findings → remediation → verification). Every claim cites participant-visible evidence.

## Measured results

All figures are measured by code in this repo; see `evaluation/evaluation_report.md` and `evaluation/usage_report.md`.

| Metric | Value |
|---|---|
| Sample `deletion_status` accuracy (32 public cases) | **100%** |
| Sample step F1 (precision / recall) | **96.5%** (99.5% / 93.8%) |
| Sample exact step-sequence match / edit similarity | 65.6% / 91.0% |
| Custom evaluation cases (hand-built mini datasets) | **7/7** pass |
| `output.csv` | 518 rows, validator **0 errors** (1 warning: case_61 has no eligible evidence) |
| Determinism | two independent runs give byte-identical output (sha256 `75b7d470…`) |
| LLM calls / tokens in the final run | **0 / 0**: every request was decided by deterministic rules |
| Cost | **$0** (local inference only; no hosted API) |
| Runtime, full 518-case run | ~7–10 s on a laptop (i7-12700H, 16 GB RAM, RTX 3060 6 GB); OCR read from cache |
| Unit tests | 110 passing (offline; a guard blocks real model calls) |

All 13 failing sample steps have a data-derived cause, and none is unexplained:
- 7 records never linked to a person in any file;
- 3 expected records absent from all participant data;
- 2 symmetric account ties;
- 1 record that appears only in activity.

## Setup (Windows Git Bash; macOS/Linux are the same with `python3`)

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt     # macOS/Linux: .venv/bin/python
cp .env.example .env    # only needed for the optional local LLM (Ollama); the pipeline runs without it
```

Put the challenge's `dataset/` folder at the repository root; it is not included in `code.zip`.

## Run

```bash
.venv/Scripts/python.exe code/main.py                 # -> output.csv (518 rows), run record appended to data/run_log.jsonl
.venv/Scripts/python.exe -m app.eval.validate_output  # contract check: rows, order, JSON, legal steps, citations
.venv/Scripts/python.exe code/evaluation/main.py      # regenerate evaluation_report.md + usage_report.md
.venv/Scripts/python.exe -m pytest -q                 # unit tests (offline)
```

**Other commands:**
- Sample check: `python -m app.pipeline.run_all --split sample && python -m app.eval.scorer --pred evaluation/sample_predictions.csv`
- Custom cases: `python scripts/make_custom_cases.py && python scripts/run_custom_cases.py`

**What a run needs:**
- No network, API key or GPU. Screenshots are read from the committed OCR cache (`data/ocr_cache/`). With an empty cache, RapidOCR runs once (~17 s per image).
- The run **fails** (exit 2, no `output.csv`) if any case cannot be assessed (crash or unresolvable identity), rather than writing a row that claims a status without proof.

## How it works

```
dataset/*.csv ──► typed loaders (Pydantic)            dataset/evidence/* ──► router
                                                        ├─ SQL (sqlite3: inventory / replica-topology / typed tables)
                                                        ├─ JSON (4 shapes)   ├─ logs (7 line formats + trace assembly)
                                                        ├─ text handover     ├─ PDF text (PyMuPDF)
                                                        └─ PNG screenshots ─► RapidOCR (cached) ─► grounded text facts
                                                                     │
                            all sources ──► one Observation model: file, locator, system, resource, time,
                                            present/absent, identifiers, attributes (+ ScopeCoverage = proof of absence)
request ─► intent rules (6 languages; LLM fallback only if ambiguous) ─► identity (current + historical, hashes)
        ─► link resources (strong identifiers ▸ OCR page ▸ deletion-workflow attribution ▸ late retained checks)
        ─► state at audit_at (file-level cutoff, per-replica timelines) ─► deletion_rules.csv verdict
        ─► status ─► planner (canonical order, citation rules) ─► output.csv ─► validator / scorer
```

| Area | Code |
|---|---|
| Evidence parsing | `app/extract/` |
| Audit core | `app/audit/` (`intent`, `identity`, `timeline`, `rules`, `flows`, `workflow`, `status`, `planner`) |
| LLM boundary | `app/llm/` (local Ollama only, temperature 0, cached, ledgered) |
| Pipeline | `app/pipeline/run_all.py` |
| Scoring and validation | `app/eval/` |

## Key decisions (details in `docs/`)

- **Deterministic first.** Code decides status, steps and order. The LLM is only a fallback for ambiguous request text: 0 cases here, and decisions would be cached in `data/intent_decisions.json`.
- **Vision LLM rejected on evidence.** Local `gemma4` vision returned schema-valid but entirely invented screens (0/74 identifiers). RapidOCR plus matching against *known* identifiers found 74/74, with 0 invented. (`docs/phase-0-setup.md`)
- **"State at audit_at" uses a file-level cutoff.** A file exported after `audit_at` never sets state or gets cited, even when its inner timestamps are earlier. This reproduces 70/70 sample citations. (`docs/conventions.md` C3)
- **Replication drift.** Each physical copy (live system, failover/read/reporting/regional replica) has its own timeline. (C12)
- **Absence needs proof:** a negative readback, or a file that enumerates the collection for that subject (subject-scoped coverage). (C11)
- **Never claim compliance without proof.** A backup or flow whose block/disable is not evidenced is a violation with the full finding → remediation → verification chain. (C17, C18)
- **Already-deleted records carry no identifiers.** They are linked through the deletion workflow's own activity, with confidence labels. Ambiguous account ties (114 eval cases) are broken deterministically and never affect status. (`docs/phase-3-audit-core.md`)

## Repository map

| Path | What |
|---|---|
| `code/main.py`, `code/evaluation/main.py` | entry points (pipeline; reports) |
| `evaluation/` and `code/evaluation/` | `evaluation_report.md`, `usage_report.md`; `custom_cases/` (7 mini datasets + `results.md`) |
| `docs/` | one doc per phase, `conventions.md` (C1–C18 with proving cases), `code-review-2026-10-24.md`, `interview-notes.md` |
| `data/` | `ocr_cache/` (OCR results), `llm_ledger.jsonl` (every LLM call), `run_log.jsonl` (every pipeline run) |
| `PROJECT_STATE.md` | decision log: every phase, failure, fix and measurement |
| `scripts/` | inventory, parse/status/error-analysis reports, custom cases, report generators, `package_submission.py` |
| `deploy/hf_static/` | case viewer as a static Hugging Face Space (private): `index.html` + `cases.json` from `scripts/export_ui_data.py`, deployed by `scripts/deploy_space.py` (HTTP API, no git, no secrets) |
| `deploy/hf_space/` | the same viewer in Gradio, run locally: `.venv-ui/Scripts/python.exe deploy/hf_space/app.py` (Gradio Spaces now need HF PRO, so the static Space is the $0 deployment) |

## Data and privacy note

All Northstar data is synthetic. No hosted LLM was used. In Phase 0 only, a local Ollama model was tried on screenshots; it is not used by the pipeline. Secrets (`HF_TOKEN`) live only in `.env`, which is git-ignored and refused by `scripts/package_submission.py`.

## Hugging Face Space Link
[Click Here!](https://huggingface.co/spaces/UnpaidLabor/northstar-deletion-audit)