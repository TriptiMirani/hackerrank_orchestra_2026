"""Resource state at audit_at, from evidence only (activity results are claims and never set state).

Eligibility (docs/conventions.md C3): an observation counts only if its FILE was captured at or before audit_at
and the observation itself is at or before audit_at. A file created later cannot change the answer, even when it
describes earlier moments.

State = the latest eligible observation that says something about existence:
  * present=True / present=False observations (readbacks, snapshots, lookups, screens);
  * absence by coverage: an eligible file that enumerates the resource's collection at time T, later than the
    last presence, and does not contain the resource. Ties at the same instant favour presence (a replica still
    holding a row the primary lost IS a remaining copy).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Callable

from pydantic import BaseModel

from app.extract.normalize import category_for
from app.schemas import EvidenceFile, Observation, ScopeCoverage


class ResourceState(BaseModel):
    system_id: str
    resource_id: str
    category: str | None
    state: str                      # PRESENT | ABSENT | UNKNOWN
    as_of: datetime | None
    evidence_id: str                # file that proves the state ("" when UNKNOWN)
    identifiers: dict[str, str] = {}
    attrs: dict = {}
    status_text: str | None = None
    basis: str = ""                 # observation | coverage | none


def eligible(o: Observation, files: dict[str, EvidenceFile], audit_at: datetime) -> bool:
    return (files[o.file_id].captured_at <= audit_at and o.observed_at is not None and o.observed_at <= audit_at)


def resolve_category(observations: list[Observation]) -> str | None:
    """Most frequent category stated by any source (time-independent: a record's category does not change)."""
    cats = Counter(o.data_category for o in observations if o.data_category)
    if not cats:
        return None
    return sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _coverage_category(c: ScopeCoverage) -> str | None:
    return category_for(c.collection.split("@")[0])


LIVE = "live"
# SQL topologies that are separate physical copies. primary/snapshot tables and every non-SQL source describe
# the live system.
REPLICA_TOPOLOGIES_LIVE = {None, "primary", "snapshot"}


def location_of_obs(o: Observation) -> str:
    topo = o.attrs.get("topology")
    return LIVE if topo in REPLICA_TOPOLOGIES_LIVE else str(topo)


def location_of_cov(c: ScopeCoverage) -> str:
    table = c.collection.split("@")[0]
    for marker in ("failover_replica", "read_replica", "reporting_replica", "replica_"):
        if marker in table:
            return table[table.index(marker):]
    return LIVE


def state_at(resource_id: str, observations: list[Observation], coverage: list[ScopeCoverage],
             files: dict[str, EvidenceFile], audit_at: datetime, system_id: str | None = None,
             applies: Callable[[ScopeCoverage], bool] = lambda c: True) -> ResourceState:
    """Replication drift: each physical copy (live system, failover/read/reporting/regional replica) has its own
    timeline. The resource remains if ANY copy's latest evidence shows it; coverage only proves absence for the
    copy it enumerates (an empty primary inventory says nothing about a failover replica)."""
    obs_all = [o for o in observations if o.present is not None and eligible(o, files, audit_at)]
    locations = sorted({location_of_obs(o) for o in obs_all} | {LIVE})
    per_loc = [_state_one(resource_id, observations, [o for o in obs_all if location_of_obs(o) == loc],
                          [c for c in coverage if location_of_cov(c) == loc and applies(c)], files, audit_at,
                          system_id)
               for loc in locations]
    present = [s for s in per_loc if s.state == "PRESENT"]
    if present:
        return max(present, key=lambda s: (s.as_of, s.evidence_id))
    absent = [s for s in per_loc if s.state == "ABSENT"]
    if absent:
        return max(absent, key=lambda s: (s.as_of, s.evidence_id))
    return per_loc[0]


def _state_one(resource_id: str, observations: list[Observation], obs: list[Observation],
               coverage: list[ScopeCoverage], files: dict[str, EvidenceFile], audit_at: datetime,
               system_id: str | None) -> ResourceState:
    category = resolve_category(observations)
    system = observations[0].system_id if observations else (system_id or "unknown")

    latest_obs_time = max((o.observed_at for o in obs), default=None)
    last_presence = max((o.observed_at for o in obs if o.present), default=None)
    files_with_resource = {o.file_id for o in observations}
    # Absence by coverage: same system, compatible collection, eligible file, after the last presence.
    absent_cov = [c for c in coverage
                  if c.system_id == system and c.captured_at is not None and c.captured_at <= audit_at
                  and files[c.file_id].captured_at <= audit_at and c.file_id not in files_with_resource
                  and (last_presence is None or c.captured_at > last_presence)
                  and (_coverage_category(c) in (None, category) or category is None)]
    latest_cov = max(absent_cov, key=lambda c: (c.captured_at, c.file_id), default=None)

    if latest_cov is not None and (latest_obs_time is None or latest_cov.captured_at > latest_obs_time):
        return ResourceState(system_id=system, resource_id=resource_id, category=category, state="ABSENT",
                             as_of=latest_cov.captured_at, evidence_id=latest_cov.file_id, basis="coverage")
    if latest_obs_time is None:
        return ResourceState(system_id=system, resource_id=resource_id, category=category, state="UNKNOWN",
                             as_of=None, evidence_id="", basis="none")

    at_latest = [o for o in obs if o.observed_at == latest_obs_time]
    present = [o for o in at_latest if o.present]
    chosen = sorted(present or at_latest, key=lambda o: (o.file_id, o.locator))
    merged_ids: dict[str, str] = {}
    merged_attrs: dict = {}
    for o in chosen:
        for k, v in o.identifiers.items():
            merged_ids.setdefault(k, v)
        for k, v in o.attrs.items():
            merged_attrs.setdefault(k, v)
    first = chosen[0]
    return ResourceState(system_id=first.system_id, resource_id=resource_id, category=category,
                         state="PRESENT" if present else "ABSENT", as_of=latest_obs_time,
                         evidence_id=first.file_id, identifiers=merged_ids, attrs=merged_attrs,
                         status_text=first.status_text, basis="observation")
