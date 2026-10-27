"""JSON evidence -> Observations. Dispatch on the document's top-level shape, never on the file name:
  * service_exports  : {system: {collection: [readback rows]}}   (service_resource_export_*)
  * snapshots        : [{captured_at, data_store, collection, records}]   (snapshot bundles)
  * collection       : {captured_at, collection, records}   (export jobs, analytics report)
  * lookup ledger    : {records: [{recorded_at, system, resource_id, record_type, result, subject_reference}]}
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.extract.lookup import lookup_observation
from app.extract.normalize import category_for, clean, parse_ts, split_record, system_for
from app.schemas import Observation, ScopeCoverage


LOOKUP_KEYS = {"system", "resource_id", "record_type", "result"}


class UnknownShape(ValueError):
    pass


def parse_json(file_id: str, path: Path, default_captured: datetime | None
               ) -> tuple[list[Observation], list[ScopeCoverage]]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if "service_exports" in doc:
        return _service_exports(file_id, doc), []
    if "snapshots" in doc:
        return _snapshots(file_id, doc)
    if "collection" in doc and "records" in doc:
        return _collection(file_id, doc, default_captured)
    if "records" in doc and all({"system", "resource_id", "result"} <= set(r) for r in doc["records"]):
        return [lookup_observation(file_id, f"records[{i}]", "json", parse_ts(r.get("recorded_at")), r["system"],
                                   r["resource_id"], r.get("record_type"), r["result"], r.get("subject_reference"))
                for i, r in enumerate(doc["records"])], []
    raise UnknownShape(f"unrecognised JSON shape, keys={sorted(doc)}")


def _obs(file_id, locator, when, system, row: dict[str, Any], present=True, category=None, status=None,
         lookup_key=None, extra_skip=frozenset()) -> Observation:
    identifiers, attrs = split_record(row, skip={"resource_id", "observed_at", *extra_skip})
    return Observation(file_id=file_id, locator=locator, source_kind="json", observed_at=when, system_id=system,
                       resource_id=row["resource_id"], present=present, data_category=category,
                       status_text=status, identifiers=identifiers, attrs=attrs, lookup_key=lookup_key)


def _service_exports(file_id: str, doc: dict) -> list[Observation]:
    out = []
    for system, collections in sorted(doc["service_exports"].items()):
        for coll, rows in sorted(collections.items()):
            for i, row in enumerate(rows):
                loc = f"service_exports.{system}.{coll}[{i}]"
                when = parse_ts(row.get("observed_at"))
                if coll == "profile_lookups":
                    # A readback by resource id: 404 = the account record is gone at observed_at.
                    profile = row.get("profile") or {}
                    merged = {"resource_id": row["resource_id"], **profile}
                    out.append(_obs(file_id, loc, when, system, merged, present=row["status_code"] == 200,
                                    category="USER_PROFILE", status=str(row["status_code"])))
                elif coll == "index_queries":
                    hits = row.get("hits") or {}
                    docs = [d for d in hits.get("documents", []) if d.get("resource_id") == row["resource_id"]]
                    merged = {"resource_id": row["resource_id"], **(docs[0] if docs else {})}
                    out.append(_obs(file_id, loc, when, system, merged, present=bool(docs),
                                    category="SEARCH_INDEX_ENTRY", status=f"hits={hits.get('total', 0)}",
                                    lookup_key=clean(row.get("query"))))
                else:
                    out.append(_obs(file_id, loc, when, system, row, category=_export_category(system, coll)))
    return out


def _export_category(system: str, coll: str) -> str | None:
    return {"analytics_platform": "ANALYTICS_PROFILE", "backup_service": "BACKUP_ARCHIVE",
            "export_service": "USER_EXPORT", "integration_service": "DATA_FLOW",
            "security_service": "SECURITY_AUDIT_LOG",
            "support_platform": "SUPPORT_MESSAGE"}.get(system)  # export 'tickets' carry a message_excerpt


def _snapshots(file_id: str, doc: dict) -> tuple[list[Observation], list[ScopeCoverage]]:
    obs, cov = [], []
    for s_i, snap in enumerate(doc["snapshots"]):
        when = parse_ts(snap.get("captured_at"))
        system, coll = snap["data_store"], snap["collection"]
        records = snap["records"]
        if records and all(LOOKUP_KEYS <= set(r) for r in records):
            # 'operational_record_history' in a store such as privacy_treatment_service: each row is a lookup
            # about ANOTHER system, with its own time and result. Not an enumeration, so no coverage claim.
            obs += [lookup_observation(file_id, f"snapshots[{s_i}].records[{r_i}]", "json",
                                       parse_ts(r.get("recorded_at")) or when, r["system"], r["resource_id"],
                                       r.get("record_type"), r["result"], r.get("lookup_value"))
                    for r_i, r in enumerate(records)]
            continue
        for r_i, row in enumerate(records):
            flat = {k: v for k, v in row.items() if not isinstance(v, dict)}
            flat.update({k: v for k, v in (row.get("document") or {}).items()})
            obs.append(_obs(file_id, f"snapshots[{s_i}].records[{r_i}]", when, system, flat,
                            category=category_for(coll), status=clean(row.get("account_status"))))
        cov.append(ScopeCoverage(file_id=file_id, system_id=system, collection=f"{coll}@snapshots[{s_i}]",
                                 captured_at=when, record_count=len(snap["records"])))
    return obs, cov


def _collection(file_id: str, doc: dict, default_captured) -> tuple[list[Observation], list[ScopeCoverage]]:
    when = parse_ts(doc.get("captured_at")) or default_captured
    coll = doc["collection"]
    obs = [_obs(file_id, f"records[{i}]", when, system_for(r["resource_id"]) or "unknown", r,
                category=category_for(coll)) for i, r in enumerate(doc["records"])]
    systems = sorted({o.system_id for o in obs})
    cov = [ScopeCoverage(file_id=file_id, system_id=s, collection=coll, captured_at=when,
                         record_count=sum(o.system_id == s for o in obs)) for s in systems]
    return obs, cov
