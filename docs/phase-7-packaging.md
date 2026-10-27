# Phase 7: Submission packaging

## What
- **`scripts/package_submission.py`** builds `code.zip` from a **whitelist**:
  - code, tests, docs and `evaluation/` (custom cases included);
  - `code/evaluation/` report copies, the README and config example;
  - `output.csv`;
  - the reproducibility data: `data/ocr_cache/`, `llm_ledger.jsonl`, `run_log.jsonl`.
  It **refuses** to build on `.env*` (except `.env.example`), nested archives, token-shaped strings (HF, Google, OpenAI, GitHub, private keys) or missing required reports.
- **`README.md`:** measured numbers first, then setup/run, architecture, decisions and repo map.
- **Entry points:** `code/main.py` (pipeline) and `code/evaluation/main.py` (rebuilds both reports and copies them to `code/evaluation/`, as the starter layout requires).
- **`docs/interview-notes.md`.**
- **`tests/unit/test_packaging.py`:** refusal tests.

## Why
`code.zip` must be runnable, secret-free and reproducible, and the reports must be where the judge looks (`code/evaluation/`). The judge ignores raw data formats, so every number lives in Markdown.

## How
`collect()` → `check()` → zip → assert there is no `.env`, `dataset/` or `.venv/` in the archive.
- **Excluded:** `dataset/`, `.venv`, `.git`, caches, `log.txt` (uploaded separately as the chat transcript), the orphaned `data/llm_cache/`, and `evaluation/output_repeat.csv`.

## Decisions & tradeoffs
- **A whitelist rather than a blacklist,** so a new file is excluded until someone decides to ship it.
- **The OCR cache is shipped,** so a fresh machine reproduces `output.csv` offline without RapidOCR's first run (~17 s per image). The cache is 176 KiB.
- **Phase 7 before Phase 6** (the developer's choice, given the 14:00 freeze): packaging and the reports are on the never-cut list.

## Proof
- **`code.zip`:** 197 files, 314 KiB; checks pass (no `.env`, `dataset/` or `.venv/`, no nested archives, no secret patterns, required reports present).
- **Fresh reproduction by the developer:**
  1. extract `code.zip` into an empty folder;
  2. copy `dataset/`;
  3. create a new Python 3.12 venv and run `pip install -r requirements.txt`;
  4. run `code/main.py`.
  - Result: 518 rows, 0 LLM calls, sha256 `75b7d4700368f2cce84f83d1ac4ffbef17cec9813e7c98083d17db170910cb91`, **byte-identical** to `output.csv`; validator 0 errors.

## How to verify it yourself (Git Bash)
```bash
cd ~/Downloads/hackerrank-orchestrate-october26
.venv/Scripts/python.exe scripts/package_submission.py
REPRO="$HOME/Downloads/repro_check"; rm -rf "$REPRO"; mkdir -p "$REPRO"
.venv/Scripts/python.exe -m zipfile -e code.zip "$REPRO" && cp -r dataset "$REPRO/" && cd "$REPRO"
py -3.12 -m venv .venv && .venv/Scripts/python.exe -m pip install -q -r requirements.txt
.venv/Scripts/python.exe code/main.py | grep output_sha256      # 75b7d470...
```

## Known limitations
- The zip assumes the challenge `dataset/` is supplied separately.
- `case_61` is COMPLETE with `[]` (no eligible evidence; validator warning).
