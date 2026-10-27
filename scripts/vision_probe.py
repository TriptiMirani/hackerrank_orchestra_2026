"""Diagnose why screen extraction returned invented content. 6 live calls, ledgered, never cached.

Each probe changes ONE variable against the same screenshot (evidence_09, search console):
  P1 free text, no system, no format         -> can the model see the image at all?
  P2 free text, our system prompt, no format -> does the system prompt derail it? (H2)
  P3 tiny JSON schema format, no system      -> does constrained decoding break vision? (H1)
  P4 full ScreenExtraction schema, no system -> same, with our real schema
  P5 free text on a native-resolution crop   -> is resolution the problem? (H3)
  P6 free text, image at 896 px              -> does a smaller image read better or worse?
  P7 ollama lib, NO think flag, NO options   -> do our overrides (think=False etc.) break vision?
  P8 raw HTTP /api/generate, no think/options -> bypasses the ollama Python package entirely
Usage: python scripts/vision_probe.py [--only P7 P8]
"""
from __future__ import annotations

import argparse
import base64
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import ollama  # noqa: E402
from PIL import Image  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.config import DATASET_DIR, LLM_LEDGER_PATH, get_settings  # noqa: E402
from app.extract.image import SYSTEM_PROMPT, prepare_image  # noqa: E402
from app.llm.client import LLMTransportError, OllamaClient  # noqa: E402
from app.llm.ledger import Ledger  # noqa: E402
from app.schemas import ScreenExtraction  # noqa: E402

IMAGE = DATASET_DIR / "evidence/images/search_index_inspector_20260901.png"
QUESTION = ("What exact text is in the search box at the top? List every email address and every _id "
            "value in the results table, exactly as written.")


class Tiny(BaseModel):
    search_box_text: str
    emails: list[str]
    ids: list[str]


def crop_native(data: bytes) -> bytes:
    # Results table + raw-document panel at original pixels (no downscale).
    img = Image.open(io.BytesIO(data)).crop((240, 0, 1800, 560))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def call_defaults(settings, img: bytes, raw_http: bool) -> tuple[str, int, int, float]:
    """Model defaults: no think flag, no options. Returns content, in, out, eval seconds."""
    if raw_http:
        r = httpx.post(f"{settings.OLLAMA_HOST}/api/generate", timeout=settings.LLM_TIMEOUT_S, json={
            "model": settings.LLM_MODEL, "prompt": QUESTION, "stream": False,
            "images": [base64.b64encode(img).decode()]})
        r.raise_for_status()
        d = r.json()
        return d.get("response", ""), d.get("prompt_eval_count", 0), d.get("eval_count", 0), d.get("eval_duration", 0) / 1e9
    resp = ollama.Client(host=settings.OLLAMA_HOST, timeout=settings.LLM_TIMEOUT_S).chat(
        model=settings.LLM_MODEL, messages=[{"role": "user", "content": QUESTION, "images": [img]}])
    return resp.message.content or "", resp.prompt_eval_count or 0, resp.eval_count or 0, (resp.eval_duration or 0) / 1e9


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="probe name prefixes to run, e.g. P7 P8")
    args = ap.parse_args()
    settings = get_settings()
    client = OllamaClient(settings)
    ledger = Ledger(LLM_LEDGER_PATH, run_label="vision_probe")
    raw = IMAGE.read_bytes()
    full = prepare_image(raw, settings.MAX_IMAGE_SIDE)
    probes = [
        ("P1_free_nosys", None, None, full),
        ("P2_free_oursys", SYSTEM_PROMPT, None, full),
        ("P3_tinyschema", None, Tiny.model_json_schema(), full),
        ("P4_fullschema", None, ScreenExtraction.model_json_schema(), full),
        ("P5_free_crop_native", None, None, crop_native(raw)),
        ("P6_free_896", None, None, prepare_image(raw, 896)),
        ("P7_lib_defaults", "DEFAULTS", None, full),
        ("P8_raw_http_defaults", "RAW_HTTP", None, full),
    ]
    if args.only:
        probes = [p for p in probes if any(p[0].startswith(o) for o in args.only)]
    print(f"run_id={ledger.run_id} model={client.model}\nexpected: search box 'email:g.silva76@gmail.com'; "
          "emails g.silva76@gmail.com, g.silva76.jr@gmail.com; ids search_428, search_1874\n")
    for name, system, schema, img in probes:
        if system in ("DEFAULTS", "RAW_HTTP"):
            # These deliberately bypass OllamaClient's overrides, so log them by hand.
            started = time.monotonic()
            content, n_in, n_out, eval_s = call_defaults(settings, img, raw_http=system == "RAW_HTTP")
            latency = round(time.monotonic() - started, 3)
            ledger.append(provider="ollama", model=settings.LLM_MODEL, purpose="vision_probe", file_id=name,
                          status="OK", attempt=1, cached=False, prompt_eval_count=n_in, eval_count=n_out,
                          latency_s=latency, eval_duration_s=round(eval_s, 3))
            hit = sum(k in content for k in ["g.silva76@gmail.com", "search_428", "search_1874"])
            print(f"== {name}: in={n_in} out={n_out} sec={latency} key_hits={hit}/3\n{content.strip()[:700]}\n",
                  flush=True)
            continue
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": QUESTION, "images": [img]}]
        started = time.monotonic()
        try:
            raw_res = client.chat(messages, schema, num_predict=512)
        except LLMTransportError as exc:
            ledger.append(provider=client.provider, model=client.model, purpose="vision_probe", file_id=name,
                          status=exc.status.value, attempt=1, cached=False, error=str(exc))
            print(f"== {name}: {exc.status.value} {exc}")
            continue
        latency = round(time.monotonic() - started, 3)
        ledger.append(provider=client.provider, model=client.model, purpose="vision_probe", file_id=name,
                      status="OK", attempt=1, cached=False, prompt_eval_count=raw_res.prompt_eval_count,
                      eval_count=raw_res.eval_count, latency_s=latency, eval_duration_s=raw_res.eval_duration_s,
                      prompt_eval_duration_s=raw_res.prompt_eval_duration_s, done_reason=raw_res.done_reason)
        hit = sum(s in raw_res.content for s in ["g.silva76@gmail.com", "search_428", "search_1874"])
        print(f"== {name}: in={raw_res.prompt_eval_count} out={raw_res.eval_count} sec={latency} "
              f"key_hits={hit}/3\n{raw_res.content.strip()[:700]}\n", flush=True)


if __name__ == "__main__":
    main()
