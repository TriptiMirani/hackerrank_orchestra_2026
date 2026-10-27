"""SQL snapshot exports -> Observations, via an in-memory SQLite database.

Three table shapes exist in the dataset:
  * inventory: `snapshot_scope(origin_system, captured_at)` + one typed table per system;
  * replicas: `customer_data_<topology>(resource_id, origin_system, collection_name, record_json, updated_at)`
    with one table per topology (primary, read/reporting/failover replica, regional replicas);
  * typed per-topology tables: `customer_profiles_primary`, `search_documents_primary`, ...
Every row becomes a present=True Observation tagged with its topology, so replication drift is visible
(the same resource present in a replica but not in the primary). Absence is left to Phase 3 via coverage.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from app.extract.normalize import category_for, clean, parse_ts, split_record, system_for
from app.schemas import Observation, ScopeCoverage

TOPOLOGY_RE = re.compile(r"^(?P<base>.*?)_(?P<topology>primary|failover_replica|read_replica|reporting_replica"
                         r"|replica_[a-z_]+)$")
CAPTURED_RE = re.compile(r"^--\s*Captured at:\s*(\S+)", re.MULTILINE)

# Typed table base name -> owning system (inventory + typed topology tables).
TABLE_SYSTEM = {
    "customer_profiles": "account_service", "customer_search_documents": "search_service",
    "search_documents": "search_service", "customer_analytics_profiles": "analytics_platform",
    "support_records": "support_platform", "customer_exports": "export_service",
    "connector_subscriptions": "integration_service", "backup_restore_entries": "backup_service",
    "security_audit_records": "security_service",
}


def split_topology(table: str) -> tuple[str, str]:
    m = TOPOLOGY_RE.match(table)
    return (m["base"], m["topology"]) if m else (table, "snapshot")


def _flatten(payload: dict) -> dict:
    """Search payloads nest identifiers under 'document'; lift one level so they are not missed."""
    flat = {k: v for k, v in payload.items() if not isinstance(v, dict)}
    for k, v in payload.items():
        if isinstance(v, dict) and k == "document":
            for dk, dv in v.items():
                flat.setdefault(dk, dv)
    return flat


def parse_sql(file_id: str, path: Path, default_captured: datetime | None
              ) -> tuple[list[Observation], list[ScopeCoverage]]:
    script = Path(path).read_text(encoding="utf-8")
    m = CAPTURED_RE.search(script)
    captured = parse_ts(m.group(1)) if m else default_captured
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(script)
        con.row_factory = sqlite3.Row
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        scope: dict[str, datetime | None] = {}
        if "snapshot_scope" in tables:
            for r in con.execute("SELECT origin_system, captured_at FROM snapshot_scope ORDER BY origin_system"):
                scope[r["origin_system"]] = parse_ts(r["captured_at"])

        obs: list[Observation] = []
        cov: list[ScopeCoverage] = []
        for table in tables:
            if table == "snapshot_scope":
                continue
            rows = [dict(r) for r in con.execute(f'SELECT * FROM "{table}" ORDER BY resource_id')]
            base, topology = split_topology(table)
            if rows and "record_json" in rows[0] or (not rows and base == "customer_data"):
                obs += _payload_rows(file_id, table, topology, rows, captured)
                # A customer_data snapshot is the subject's data across stores; its customer profile store is
                # covered even when it holds no row (an empty profile store = the subject's profile is gone, as
                # with an empty customer_profiles snapshot in a bundle, case_31). Still subject-scoped (C11).
                for system in sorted({r["origin_system"] for r in rows} | {"account_service"}):
                    cov.append(ScopeCoverage(file_id=file_id, system_id=system, collection=table,
                                             captured_at=captured,
                                             record_count=sum(r["origin_system"] == system for r in rows)))
                continue
            system = TABLE_SYSTEM.get(base)
            when = scope.get(system, captured) if system else captured
            for i, row in enumerate(rows):
                rid = row["resource_id"]
                sys_id = system or system_for(rid) or "unknown"
                identifiers, attrs = split_record(row, skip={"resource_id"})
                attrs["topology"] = topology
                obs.append(Observation(
                    file_id=file_id, locator=f"{table}#{i + 1}", source_kind="sql", observed_at=when,
                    system_id=sys_id, resource_id=rid, present=True, data_category=category_for(base),
                    status_text=clean(row.get("account_status")), identifiers=identifiers, attrs=attrs))
            # Inventory tables enumerate a whole collection only for systems listed in snapshot_scope.
            if system and (system in scope or not scope):
                # typed tables in a point-in-time DB snapshot (no snapshot_scope) enumerate the whole table
                cov.append(ScopeCoverage(file_id=file_id, system_id=system, collection=table,
                                         captured_at=when, record_count=len(rows), scoped=bool(scope)))
        return obs, cov
    finally:
        con.close()


def _payload_rows(file_id, table, topology, rows, captured) -> list[Observation]:
    out = []
    for i, row in enumerate(rows):
        payload = json.loads(row["record_json"])
        identifiers, attrs = split_record(_flatten(payload), skip={"resource_id"})
        attrs.update({"topology": topology, "collection": row["collection_name"], "updated_at": row["updated_at"]})
        out.append(Observation(
            file_id=file_id, locator=f"{table}#{i + 1}", source_kind="sql", observed_at=captured,
            system_id=row["origin_system"], resource_id=row["resource_id"], present=True,
            data_category=category_for(row["collection_name"]),
            status_text=clean(payload.get("account_status")), identifiers=identifiers, attrs=attrs))
    return out
