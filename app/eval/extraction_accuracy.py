"""Score an LLM screen extraction against hand-verified expected identifiers.

Placement-agnostic on purpose: a value counts as found if it appears anywhere in the
extraction, because downstream code will search all extracted strings anyway. What matters
is that identifiers are transcribed exactly, and that none are invented.
"""
from __future__ import annotations

import re
from typing import Any

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
USER_ID_RE = re.compile(r"\buser_\d+\b")
CATEGORIES = ["names", "emails", "phones", "user_ids", "resource_ids"]


def all_strings(data: Any) -> list[str]:
    if isinstance(data, str):
        return [data]
    if isinstance(data, dict):
        return [s for v in data.values() for s in all_strings(v)]
    if isinstance(data, list):
        return [s for v in data for s in all_strings(v)]
    return []


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def score_extraction(data: dict[str, Any], expected: dict[str, list[str]]) -> dict[str, Any]:
    strings = all_strings(data)
    blob = _norm("\n".join(strings))
    blob_digits = [_digits(s) for s in strings]

    found, missed = 0, []
    for cat in CATEGORIES:
        for value in expected.get(cat, []):
            if cat == "phones":
                hit = any(_digits(value) and _digits(value) in d for d in blob_digits)
            else:
                hit = _norm(value) in blob
            if hit:
                found += 1
            else:
                missed.append(f"{cat}:{value}")

    # Hallucination check: identifiers the model produced that are not on the screen.
    exp_emails = {e.casefold() for e in expected.get("emails", [])}
    exp_users = set(expected.get("user_ids", []))
    joined = "\n".join(strings)
    invented = sorted({e for e in EMAIL_RE.findall(joined) if e.casefold() not in exp_emails}
                      | {u for u in USER_ID_RE.findall(joined) if u not in exp_users})

    total = sum(len(expected.get(c, [])) for c in CATEGORIES)
    return {"found": found, "expected": total, "recall": round(found / total, 3) if total else 1.0,
            "missed": missed, "invented": invented}
