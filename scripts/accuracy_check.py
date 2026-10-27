"""Score screenshot transcription against hand-verified identifiers (tests/fixtures/accuracy_expected.json).

--engine ocr (default): RapidOCR, deterministic, no LLM, no tokens. ~13 s/image on CPU when uncached.
--engine llm: the local vision model. Calls Ollama: get an estimate and the developer's OK first (hard rule 5).

Usage:
  python scripts/accuracy_check.py                         # OCR, every file with expectations
  python scripts/accuracy_check.py --files evidence_09 --show
  python scripts/accuracy_check.py --engine llm            # kept for comparison; gemma4 vision is broken here
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from app.config import DATASET_DIR, LLM_CACHE_DIR, LLM_LEDGER_PATH, OCR_CACHE_DIR, REPO_ROOT  # noqa: E402
from app.eval.extraction_accuracy import score_extraction  # noqa: E402
from app.llm.cache import LLMCache  # noqa: E402

EXPECTED_PATH = REPO_ROOT / "tests" / "fixtures" / "accuracy_expected.json"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"


def run_ocr(fid: str, path: Path, fresh: bool) -> dict:
    from app.extract.ocr import ocr_file
    res = ocr_file(fid, path, LLMCache(OCR_CACHE_DIR), fresh=fresh)
    data = {"lines": res.texts()} if res.status.value == "OK" else None
    return {"status": res.status.value, "cached": res.cached, "sec": res.seconds, "in": 0, "out": 0,
            "data": data, "error": res.error, "raw": res.model_dump(mode="json")}


def make_llm_runner():
    from app.config import get_settings
    from app.extract.image import extract_screen
    from app.llm.client import build_extractor
    from app.llm.ledger import Ledger
    settings = get_settings()
    extractor = build_extractor(settings, Ledger(LLM_LEDGER_PATH), LLMCache(LLM_CACHE_DIR))

    def run(fid: str, path: Path, fresh: bool) -> dict:
        res = extract_screen(extractor, fid, path, settings.MAX_IMAGE_SIDE, settings.LLM_NUM_PREDICT, fresh=fresh)
        return {"status": res.status.value, "cached": res.cached, "sec": res.latency_s,
                "in": res.prompt_eval_count, "out": res.eval_count, "data": res.data, "error": res.error,
                "raw": res.data}
    return run


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["ocr", "llm"], default="ocr")
    ap.add_argument("--files", nargs="*", help="file_ids to run (default: all with expectations)")
    ap.add_argument("--show", action="store_true", help="print the transcription")
    ap.add_argument("--fresh", action="store_true", help="bypass the cache")
    args = ap.parse_args()

    expected = {k: v for k, v in json.loads(EXPECTED_PATH.read_text(encoding="utf-8")).items()
                if not k.startswith("_")}
    manifest = pd.read_csv(DATASET_DIR / "evidence_files.csv", dtype=str).set_index("file_id")
    file_ids = args.files or sorted(expected)
    runner = run_ocr if args.engine == "ocr" else make_llm_runner()
    fixture_dir = FIXTURE_DIR / ("ocr_extractions" if args.engine == "ocr" else "screen_extractions")
    fixture_dir.mkdir(parents=True, exist_ok=True)
    print(f"engine={args.engine}")

    rows = []
    for fid in file_ids:
        r = runner(fid, DATASET_DIR / manifest.loc[fid, "file_path"], args.fresh)
        score = score_extraction(r["data"], expected[fid]) if r["data"] and fid in expected else None
        if r["data"] and not r["cached"]:
            (fixture_dir / f"{fid}.json").write_text(json.dumps(r["raw"], indent=2, ensure_ascii=False),
                                                     encoding="utf-8")
        rows.append((fid, r, score))
        print(f"{fid:12s} {r['status']:10s} cached={r['cached']!s:5s} sec={r['sec']:6.1f} "
              + (f"recall={score['found']}/{score['expected']} missed={score['missed']} invented={score['invented']}"
                 if score else f"error={r['error']}"), flush=True)
        if args.show and r["data"]:
            print(json.dumps(r["data"], indent=2, ensure_ascii=False))

    print("\n| file_id | status | cached | sec | in_tok | out_tok | recall | missed | invented |")
    print("|---|---|---|---|---|---|---|---|---|")
    for fid, r, s in rows:
        print(f"| {fid} | {r['status']} | {r['cached']} | {r['sec']:.1f} | {r['in']} | {r['out']} | "
              + (f"{s['found']}/{s['expected']} | {', '.join(s['missed']) or '-'} | {', '.join(s['invented']) or '-'} |"
                 if s else "- | - | - |"))
    live = [r for _, r, _ in rows if not r["cached"]]
    scored = [s for _, _, s in rows if s]
    if live:
        print(f"\nuncached: {len(live)}  mean sec/image: {sum(r['sec'] for r in live) / len(live):.1f}")
    if scored:
        f, t = sum(s["found"] for s in scored), sum(s["expected"] for s in scored)
        print(f"overall recall: {f}/{t} = {f / t:.1%}   invented identifiers: "
              f"{sum(len(s['invented']) for s in scored)}")


if __name__ == "__main__":
    main()
