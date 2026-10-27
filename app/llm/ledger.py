"""Append-only JSONL ledger of every LLM call attempt and cache hit.

usage_report.py builds evaluation/usage_report.md from this file only, so every number in
that report traces back to a line here.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel


def new_run_id() -> str:
    """RUN_ID env var wins so a shell script can tag a whole run; otherwise a fresh id."""
    return os.environ.get("RUN_ID") or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    )


class LedgerEntry(BaseModel):
    timestamp: str
    run_id: str
    run_label: str
    provider: str
    model: str
    purpose: str
    file_id: str
    status: str  # OK | CACHE_HIT | PARSE_ERROR | EXTRACTION_FAILED | CONTEXT_OVERFLOW | UNREACHABLE | TIMEOUT
    attempt: int
    cached: bool
    prompt_eval_count: int = 0
    eval_count: int = 0
    latency_s: float = 0.0
    load_duration_s: float = 0.0
    prompt_eval_duration_s: float = 0.0
    eval_duration_s: float = 0.0
    done_reason: str | None = None
    cache_key: str = ""
    error: str | None = None
    # True for bookkeeping lines that are NOT a model call (the EXTRACTION_FAILED summary after the retries).
    summary: bool = False


class Ledger:
    def __init__(self, path: Path, run_id: str | None = None, run_label: str | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or new_run_id()
        # RUN_LABEL separates e.g. "cold" (cache-filling) from "final" runs in the report.
        self.run_label = run_label or os.environ.get("RUN_LABEL", "dev")

    def append(self, **fields) -> LedgerEntry:
        entry = LedgerEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            run_id=self.run_id,
            run_label=self.run_label,
            **fields,
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(entry.model_dump_json() + "\n")
            fh.flush()
        return entry

    def read_all(self) -> list[LedgerEntry]:
        return read_ledger(self.path)


def read_ledger(path: Path) -> list[LedgerEntry]:
    path = Path(path)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(LedgerEntry.model_validate_json(line))
    return entries
