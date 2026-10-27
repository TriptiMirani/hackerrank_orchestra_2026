# Phase 0: Environment, data recon, LLM plumbing, accuracy check

## What
- Python 3.12 venv with every dependency pinned with `==` in `requirements.txt`, and `.env` / `.env.example`.
- `app/config.py`: typed settings that fail fast when a required key is missing.
- `app/llm/client.py`: the LLM boundary. It has three parts:
  - `OllamaClient`, which refuses `cloud` models and any host other than localhost, and sets temperature 0, a fixed seed, `think=False`, explicit `num_ctx`/`num_predict` and `keep_alive` on every call;
  - `FakeLLMClient` for tests;
  - `StructuredExtractor`, which runs cache → call → Pydantic validation → retry with error feedback (at most 2 retries) → ledger.
- `app/llm/ledger.py`: an append-only JSONL record of every call attempt and cache hit. `app/llm/cache.py` is a content-addressed cache.
- `app/extract/ocr.py`: RapidOCR for screenshots. It is cached, and a failure returns an `OCR_FAILED` status. `app/extract/pdf.py` uses PyMuPDF to extract text, detect scanned pages and rasterize them.
- Scripts:
  - `inventory.py`: dataset profile;
  - `usage_report.py`: usage report built from the ledger only;
  - `accuracy_check.py`: OCR or LLM against hand-verified ground truth;
  - `vision_probe.py`: the diagnostic probe for the vision failure.

## Why
Every later phase depends on these pieces. The ledger also had to exist before the first model call, so that the usage numbers are measured rather than estimated.

## How
- **Cache key:** `sha256(file bytes) + model/engine + prompt or OCR version + options + schema`. A hit costs 0 tokens and 0 s, and is still written to the ledger as `CACHE_HIT`, so the final run's usage is measured. Writes go to a temp file and are then renamed, so a run interrupted with Ctrl+C can resume safely.
- **Retries:** at temperature 0, the same prompt returns the same output. A retry therefore adds the bad reply and the validation error to the conversation, so the next prompt is different.
- **Strict schemas:** `extra="forbid"`, so a field the model invents (such as a "verdict") fails validation.
- **Context overflow:** a call whose `prompt_eval_count` reaches 95% or more of `num_ctx` returns `CONTEXT_OVERFLOW` instead of an answer.
- **Unit-test guard:** `tests/unit/conftest.py` replaces `ollama.Client` with a class that raises, so a unit test cannot reach the model.

## Decisions & tradeoffs
- **gemma4 vision → RapidOCR.** gemma4 returned schema-valid but entirely invented consoles: 0/74 recall and 28 invented identifiers. The probe varied one thing at a time (with and without format, system prompt, crop, size, model defaults, raw HTTP), and the model still said it could not see the image. Its vision does not work in this install. RapidOCR scored 74/74 with 0 invented, and gives identical output across runs.
  - Cost: about 17 s per image on CPU, and the text has no structure.
  - Benefit: it is deterministic, needs no LLM, and puts every value at a position in the image.
  - Phase 2 matches **known** identifiers against the OCR lines.
- **Lesson:** schema validation catches malformed output, not hallucinated output. Only the hand-verified accuracy check caught the failure.
- **Python 3.12, not 3.13:** for wheel coverage (onnxruntime). The transitive OCR dependencies (onnxruntime, opencv-python, numpy) are pinned too, because they decide the OCR output.
- **Old cache entries:** the bad `screen-v1` LLM results were orphaned by a prompt-version bump, not deleted. Their copies are kept in `tests/fixtures/failed_screen-v1/`.
- **Failures are never cached,** so a fix takes effect on the next run.
- **Ground truth:** `tests/fixtures/accuracy_expected.json` was written by Claude Code by viewing the 11 PNGs, and spot-checked by the developer.

## How to verify it yourself (Git Bash)
```bash
cd ~/Downloads/hackerrank-orchestrate-october26
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pytest -q                       # 40 passed, 1 skipped (live)
.venv/Scripts/python.exe scripts/inventory.py
.venv/Scripts/python.exe scripts/accuracy_check.py          # OCR: 74/74, 0 invented (cached: 0 s)
.venv/Scripts/python.exe scripts/usage_report.py
LLM_MODEL=gemma4:cloud .venv/Scripts/python.exe -c "from app.config import get_settings; from app.llm.client import OllamaClient; OllamaClient(get_settings())"   # LLMConfigError
RUN_LIVE=1 .venv/Scripts/python.exe -m pytest tests/live -q # calls Ollama: estimate + OK first
```

## Known limitations
- gemma4 vision is unusable here. The vision code path stays only as the failure record.
- OCR drops some spaces (`01Sep202606:00:14UTC`) and reads the search icon as a leading `0`. Matching must normalise whitespace, compare phones by digits, and accept only known identifiers.
- The context-overflow check runs after the call. Ollama prompt caching could make `prompt_eval_count` too low; prompts here are small (about 900 tokens out of 8192).
- The OCR text has no layout structure yet; Phase 2 adds the facts.
