# Phase 6: Case viewer UI and Hugging Face Space

## What
- **`scripts/export_ui_data.py`** runs the same audit code as the final run and writes `cases.json`: 518 eval + 32 sample cases. For each case it stores:
  - the request and the resolved identifiers;
  - every linked resource's state at `audit_at`, with evidence file, verdict and link reason;
  - the steps;
  - for samples, the expected answer;
  - plus the measured scores.
  It **asserts that the eval steps are identical to `output.csv`**, so the viewer shows exactly what was submitted.
- **`deploy/hf_static/`:** a static viewer (`index.html`, vanilla JS, no libraries, light/dark), deployed as a **private** static Space at https://huggingface.co/spaces/UnpaidLabor/northstar-deletion-audit.
- **`deploy/hf_space/`:** the same viewer in Gradio, for local use (`.venv-ui/Scripts/python.exe deploy/hf_space/app.py`).
- **`scripts/deploy_space.py`:** HTTP-API upload (no git). It reads `HF_TOKEN` from `.env` without printing it, creates the Space as private, refuses to upload if the repo is public, and adds no secrets or variables.

## Why
The AI interview and the reviewers need to *see* the evidence trail behind each answer: who the subject is, which records were found where, the state at `audit_at`, and why each step exists. They should not have to read JSON to do it.

## How
1. Run the audit once.
2. Export a read-only JSON.
3. A static page reads the JSON.

No pipeline, dataset, model or secret runs in the Space.

## Decisions & tradeoffs
- **Static, not Gradio, on Hugging Face.** Creating a Gradio Space returned `402 Payment Required`: Gradio and Docker Spaces on free CPU now require PRO, while static Spaces are free. The developer chose the static option to keep the $0 rule. The Gradio app is kept for local use.
- **Separate `.venv-ui`.** Gradio 6.30 conflicts with the pipeline's pinned `pillow==13.0.0`. The pipeline's `requirements.txt` was left unchanged, so `code.zip` still reproduces `output.csv` byte for byte. The UI has its own `deploy/hf_space/requirements.txt`.
- **Precomputed export instead of running the pipeline in the Space.** It is smaller, needs no dataset upload and has no heavy dependencies. The row-for-row equality check against `output.csv` keeps it honest.
- **Private Space**, as the developer requested. It is verified: an unauthenticated API request returns 401.

## Proof
- **Space:** `UnpaidLabor/northstar-deletion-audit`: `private=True`, `sdk=static`, stage `RUNNING`. Files: `index.html`, `cases.json`, `README.md` (plus Hugging Face's default `style.css`, unused).
- **Security and data:** unauthenticated API request → 401. Space variables: none. No secrets added.
- **Export:** 550 cases, 1.7 MB; eval steps identical to `output.csv`.
- **Local checks:** Gradio app launched with HTTP 200. The static page serves 200 for the page and the data. `node --check` on the page script passes.
- **Browser walk-through by the developer:** case_39 renders correctly. It shows `data_flow_1121` with verdict ACTIVE_FLOW citing `evidence_14` (the connector screenshot), and the finding → disable → verify steps.

## How to verify it yourself (Git Bash)
```bash
cd ~/Downloads/hackerrank-orchestrate-october26
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/export_ui_data.py       # "eval steps identical to output.csv: True"
cp deploy/hf_space/cases.json deploy/hf_static/cases.json
(cd deploy/hf_static && ../../.venv/Scripts/python.exe -m http.server 8765)      # open http://127.0.0.1:8765
.venv-ui/Scripts/python.exe deploy/hf_space/app.py                              # Gradio version, local
.venv-ui/Scripts/python.exe scripts/deploy_space.py --sdk static                # re-deploy (private)
```

## Known limitations
- The Space is a snapshot: re-run the export and deploy after any change to `output.csv`.
- It is private, so viewers need access granted by the owner.
- The static page and the Gradio app duplicate their rendering logic.
