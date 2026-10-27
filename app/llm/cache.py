"""Content-addressed cache for LLM results.

Key = sha256 over (input file hashes, model, prompt_version, options, schema name).
A hit costs 0 tokens and 0 seconds. Only successful, schema-valid results are stored,
so a failure is retried on the next run instead of being frozen in place.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def make_cache_key(
    input_hashes: list[str],
    model: str,
    prompt_version: str,
    options: dict[str, Any],
    schema_name: str,
) -> str:
    payload = {
        "inputs": input_hashes,
        "model": model,
        "prompt_version": prompt_version,
        "options": options,
        "schema": schema_name,
    }
    # sort_keys + fixed separators: the same logical key always hashes the same.
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(canonical.encode("utf-8"))


class LLMCache:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put(self, key: str, record: dict[str, Any]) -> None:
        # Write to a temp file then rename, so Ctrl+C mid-write never leaves a
        # half-written entry that a resumed run would trust.
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, sort_keys=True, indent=2), encoding="utf-8")
        os.replace(tmp, path)
