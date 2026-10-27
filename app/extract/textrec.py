"""Plain-text records (shift handover) -> Observations + TextLines.

Structured lines look like:  - 2026-10-01T12:00:00Z | account_service | account_146 | USER_PROFILE | NOT_FOUND | e.x@y.com
Every line is also kept as a TextLine, so nothing unstructured is lost.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.extract.lookup import lookup_observation
from app.extract.normalize import parse_ts
from app.schemas import Observation, TextLine

ROW_RE = re.compile(r"^\s*-\s*(\S+)\s*\|\s*([\w]+)\s*\|\s*([\w-]+)\s*\|\s*([A-Z_]+)\s*\|\s*([A-Z_]+)\s*\|\s*(.+?)\s*$")


def parse_text(file_id: str, path: Path) -> tuple[list[Observation], list[TextLine]]:
    obs, lines = [], []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        lines.append(TextLine(file_id=file_id, locator=f"line {i}", text=line.strip()))
        if m := ROW_RE.match(line):
            ts, system, rid, rtype, result, subject = m.groups()
            obs.append(lookup_observation(file_id, f"line {i}", "text", parse_ts(ts), system, rid, rtype, result,
                                          subject))
    return obs, lines
