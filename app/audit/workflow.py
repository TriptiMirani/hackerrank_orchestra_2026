"""Attribute the deletion workflow's own activity (deletion_worker / privacy_intake) to requests.

Why this exists: a record that is already compliant (account deleted, analytics anonymized) holds no identifier,
so no evidence links it to the person. The only link is the workflow that acted on it. Activity rows carry no
person either, so attribution is inferred, and every link carries a confidence:

  * window      : event time within WINDOW of the request's reported_at (the workflow reports after acting;
                  failed/retried steps can land after reported_at);
  * agreement   : for the account deletion, its reported_result mirrors the request's reported_status
                  (COMPLETE~SUCCESS, PARTIALLY_COMPLETE~PARTIAL, NOT_STARTED~FAILED). This uses the workflow's
                  claim only to ATTRIBUTE the event, never as proof of state;
  * exclusivity : each account deletion belongs to one request; a resource strongly linked (by identifiers) to a
                  different user is never attributed here.
Constraint propagation assigns forced choices first; remaining ties are broken deterministically (closest to
reported_at, then resource id) and marked confidence="tie".
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from app.schemas import DeletionRequest, SystemActivity

WINDOW = timedelta(hours=2)
WORKFLOW_SOURCES = {"deletion_worker", "privacy_intake"}
RESULT_FOR_STATUS = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}


@dataclass(frozen=True)
class WorkflowLink:
    case_id: str
    activity: SystemActivity
    confidence: str          # "unique" | "propagated" | "tie"


def _in_window(req: DeletionRequest, a: SystemActivity) -> bool:
    return abs(a.event_time - req.reported_at) <= WINDOW


def _distance(req: DeletionRequest, a: SystemActivity) -> float:
    return abs((a.event_time - req.reported_at).total_seconds())


def attribute_workflow(requests: list[DeletionRequest], activity: tuple[SystemActivity, ...],
                       owner_of: dict[str, str], user_of_case: dict[str, str],
                       evidence_hint: dict[str, set[str]] | None = None,
                       non_deletion: set[str] | None = None) -> dict[str, list[WorkflowLink]]:
    """owner_of: resource_id -> user_id for resources strongly linked by evidence (identifiers).
    user_of_case: case_id -> resolved user_id.
    evidence_hint: case_id -> resource ids read back in an eligible file that mentions the subject. Among
    candidate accounts, those the case's own evidence actually observed come first (grounded, not timing).
    non_deletion: case_ids whose request does not authorize deletion. They may only claim the intake's
    CLASSIFY_REQUEST event (and deletion requests never do), so they cannot take a deletion request's records.
    Returns case_id -> workflow links."""
    evidence_hint = evidence_hint or {}
    non_deletion = non_deletion or set()
    events = [a for a in activity if a.source_system in WORKFLOW_SOURCES]
    by_id = {r.case_id: r for r in requests}

    def allowed(case_id: str, a: SystemActivity) -> bool:
        owner = owner_of.get(a.resource_id)
        return owner is None or owner == user_of_case[case_id]

    links: dict[str, list[WorkflowLink]] = defaultdict(list)

    # 1. Account deletions (and intake classifications): exactly one per request, via constraint propagation.
    acct = [a for a in events if a.resource_id.startswith("account_")
            and a.event_type in ("DELETE_RECORD", "CLASSIFY_REQUEST")]
    cands: dict[str, list[SystemActivity]] = {}
    for r in requests:
        exp = RESULT_FOR_STATUS[r.reported_status]
        wanted = "CLASSIFY_REQUEST" if r.case_id in non_deletion else "DELETE_RECORD"
        pool = [a for a in acct if a.event_type == wanted and _in_window(r, a) and allowed(r.case_id, a)]
        # an account strongly owned by this subject wins outright
        own = [a for a in pool if owner_of.get(a.resource_id) == user_of_case[r.case_id]]
        agree = [a for a in pool if a.event_type == "CLASSIFY_REQUEST" or a.reported_result == exp]
        best = agree or pool
        seen = [a for a in best if a.resource_id in evidence_hint.get(r.case_id, set())]
        cands[r.case_id] = own or seen or best
    assigned: dict[str, tuple[SystemActivity, str]] = {}
    taken: set[str] = set()
    changed = True
    while changed:
        changed = False
        for cid in sorted(cands):
            if cid in assigned:
                continue
            live = [a for a in cands[cid] if a.activity_id not in taken]
            if len(live) == 1:
                assigned[cid] = (live[0], "unique" if len(cands[cid]) == 1 else "propagated")
                taken.add(live[0].activity_id)
                changed = True
    for cid in sorted(cands):  # deterministic tie-break for what propagation could not decide
        if cid in assigned:
            continue
        live = sorted((a for a in cands[cid] if a.activity_id not in taken),
                      key=lambda a: (_distance(by_id[cid], a), a.resource_id))
        if live:
            assigned[cid] = (live[0], "tie")
            taken.add(live[0].activity_id)
    for cid, (a, conf) in assigned.items():
        links[cid].append(WorkflowLink(cid, a, conf))

    # 2. Other workflow actions on resources with no strong owner (e.g. an analytics profile anonymized before any
    #    evidence saw it). A request's workflow acts at most once per (event type, target system), so this is
    #    the same propagation: an event whose candidate requests shrink to one is assigned; no tie-breaking here
    #    (an unforced guess would hand one person's record to another).
    pending = [a for a in events if not a.resource_id.startswith("account_") and a.resource_id not in owner_of]
    slot_taken: set[tuple[str, str, str]] = set()   # (case_id, event_type, target_system)
    done: set[str] = set()
    changed = True
    while changed:
        changed = False
        for a in pending:
            if a.activity_id in done:
                continue
            live = [r for r in requests if r.case_id not in non_deletion and _in_window(r, a)
                    and (r.case_id, a.event_type, a.target_system) not in slot_taken]
            if len(live) == 1:
                r = live[0]
                links[r.case_id].append(WorkflowLink(r.case_id, a, "propagated"))
                slot_taken.add((r.case_id, a.event_type, a.target_system))
                done.add(a.activity_id)
                changed = True
    return dict(links)
