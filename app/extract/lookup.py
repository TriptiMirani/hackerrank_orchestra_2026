"""The 'record lookup' row shape shared by JSON ledgers, two .log files and the handover .txt:
    recorded_at | system | resource_id | record_type | result | subject_reference
"""
from __future__ import annotations

from datetime import datetime

from app.extract.normalize import classify_reference, clean
from app.schemas import Observation, SourceKind

# result -> was the record there? ANONYMIZED/REDACTED/BLOCKED/DISABLED records still exist, in a treated state.
RESULT_PRESENT = {
    "FOUND": True, "IDENTIFIABLE": True, "ACTIVE": True, "ANONYMIZED": True, "REDACTED": True,
    "BLOCKED": True, "DISABLED": True, "NOT_FOUND": False,
}


def lookup_observation(file_id: str, locator: str, kind: SourceKind, recorded_at: datetime | None,
                       system: str, resource_id: str, record_type: str | None, result: str,
                       subject: str | None) -> Observation:
    subject = clean(subject)
    identifiers: dict[str, str] = {}
    attrs: dict[str, str | None] = {}
    if subject:
        ref_kind = classify_reference(subject)
        if ref_kind:
            identifiers[ref_kind] = subject  # case kept as written; matching normalizes later
        else:
            attrs["subject_reference"] = subject  # e.g. "no direct identifier"
    result = result.strip().upper()
    return Observation(
        file_id=file_id, locator=locator, source_kind=kind, observed_at=recorded_at, system_id=system.strip(),
        resource_id=resource_id.strip(), present=RESULT_PRESENT.get(result), data_category=clean(record_type),
        status_text=result, identifiers=identifiers, attrs=attrs, lookup_key=subject)
