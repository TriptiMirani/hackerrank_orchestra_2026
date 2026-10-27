"""Case assessment: which resources belong to the subject, their state at audit_at, the policy verdict for
each, and the case status. Everything here is deterministic; no LLM decides status (hard rule 6).

Linking, strongest first:
  strong   : an eligible observation (file captured <= audit_at) whose identifiers or lookup key belong to the
             subject, or a data_catalog LINK_RESOURCE_IDENTITY activity naming the subject (<= audit_at);
  page     : an OCR/PDF record with no identifiers of its own, on a page about exactly this subject;
  workflow : the deletion workflow acted on it for this request (app/audit/workflow.py). Only reaches records
             that are already compliant: a violation carries identifiers, so it is always strongly linked.
"""
from __future__ import annotations

import re
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel

from app.audit.flows import flow_active, restore_open
from app.audit.identity import Identity, UnresolvedIdentity, resolve_identity
from app.audit.intent import (Intent, IntentResult, classify_intent, classify_with_llm, decision_key,
                              from_decision, load_decisions)
from app.config import DATA_DIR
from app.audit.rules import FLOW_CATEGORY, exposed_identifiers, treatment_by_category
from app.audit.timeline import ResourceState, eligible, state_at
from app.audit.workflow import WorkflowLink, attribute_workflow
from app.extract.lexicon import Lexicon, build_lexicon, email_key, name_key, phone_key
from app.extract.textfacts import PREFIX_CATEGORY
from app.ingest.evidence_index import EvidenceIndex, get_index, subject_keys
from app.ingest.loaders import (load_activity, load_audit_log, load_identities, load_requests, load_rules,
                                load_sample_requests)
from app.schemas import DeletionRequest, Observation, ScopeCoverage, SystemActivity

LINK_RE = re.compile(r"linked (\S+) \(.*?\) to (\S+) and (user_\d+)|confirmed (\S+) \(.*?\) belongs only to (\S+) "
                     r"and (user_\d+)")


class Verdict(str, Enum):
    REMAINING = "REMAINING"            # must be deleted, still present
    EXPOSED = "EXPOSED"                # kept record still holds forbidden identifiers
    ACTIVE_FLOW = "ACTIVE_FLOW"
    RESTORE_RISK = "RESTORE_RISK"
    ABSENT_OK = "ABSENT_OK"
    REDACTED_OK = "REDACTED_OK"
    ANONYMIZED_OK = "ANONYMIZED_OK"
    RETAINED_OK = "RETAINED_OK"
    FLOW_DISABLED_OK = "FLOW_DISABLED_OK"
    RESTORE_BLOCKED_OK = "RESTORE_BLOCKED_OK"
    UNKNOWN = "UNKNOWN"                # no eligible evidence of state


VIOLATIONS = {Verdict.REMAINING, Verdict.EXPOSED, Verdict.ACTIVE_FLOW, Verdict.RESTORE_RISK}


class Item(BaseModel):
    resource: ResourceState
    category: str
    treatment: str
    verdict: Verdict
    link: str
    link_detail: str
    exposed: list[str] = []


class CaseAssessment(BaseModel):
    case_id: str
    status: str                        # COMPLETE | INCOMPLETE | NOT_APPLICABLE | ERROR (never written to output)
    intent: IntentResult
    rule_intent: str = ""              # what the deterministic rules said (AMBIGUOUS stays visible after fallback)
    identity: Identity | None = None
    items: list[Item] = []
    unresolved: list[Item] = []        # linked records whose state/setting the evidence does not establish
    notes: list[str] = []


@dataclass
class AuditContext:
    index: EvidenceIndex
    lexicon: Lexicon
    treatments: dict[str, str]
    activity: tuple[SystemActivity, ...]
    owner_of: dict[str, str]
    catalog_links: dict[str, list[tuple[SystemActivity, str, str]]]
    identities: tuple = ()
    audit_log: tuple = ()
    # Built lazily on the first AMBIGUOUS request; None when no LLM is configured (e.g. the HF Space).
    intent_extractor_factory: object = None
    _intent_extractor: object = None
    workflow: dict[str, list[WorkflowLink]] = field(default_factory=dict)
    coverage_by_system: dict[str, list[ScopeCoverage]] = field(default_factory=dict)


def _users_for(kind: str, value: str, lex: Lexicon) -> set[str]:
    if kind == "email":
        return set(lex.emails.get(email_key(value), ()))
    if kind == "phone":
        return set(lex.phones.get(phone_key(value), ()))
    if kind == "name":
        return set(lex.names.get(name_key(value), ()))
    if kind == "email_hash":
        return set(lex.hashes.get(value.lower(), ()))
    if kind == "user_ref":
        return {value} if value in lex.user_ids else set()
    return set()


def _key_users(value: str, lex: Lexicon) -> set[str]:
    return set().union(*(_users_for(k, value, lex) for k in ("email", "user_ref", "email_hash", "phone", "name")))


def build_ownership(index: EvidenceIndex, lex: Lexicon, catalog: dict) -> dict[str, str]:
    """resource -> the single user any evidence ties it to. Used only to keep the workflow attribution from
    handing one person's record to another. Ownership is static, so all files are used here; STATE never is."""
    users: dict[str, set[str]] = defaultdict(set)
    for rid, obs in index.by_resource.items():
        for o in obs:
            for k, v in o.identifiers.items():
                users[rid] |= _users_for(k, v, lex)
            if o.lookup_key:
                users[rid] |= _key_users(o.lookup_key, lex)
    for rid, links in catalog.items():
        for _a, _key, uid in links:
            users[rid].add(uid)
    return {rid: next(iter(u)) for rid, u in users.items() if len(u) == 1}


def parse_catalog_links(activity: tuple[SystemActivity, ...]) -> dict[str, list[tuple[SystemActivity, str, str]]]:
    out: dict[str, list] = defaultdict(list)
    for a in activity:
        if a.event_type == "LINK_RESOURCE_IDENTITY" and (m := LINK_RE.search(a.details)):
            rid, key, uid = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(4), m.group(5), m.group(6))
            out[a.resource_id].append((a, key, uid))
    return dict(out)


def _accounts_seen_with_subject(req: DeletionRequest, ident: Identity, index: EvidenceIndex) -> set[str]:
    """Account ids read back in eligible files (captured <= audit_at) that also mention this subject."""
    subj = subject_keys(ident.emails + [ident.user_id] + ident.email_hashes + ident.phones + ident.names)
    files = {f for f, keys in index.file_subjects.items()
             if keys & subj and index.files[f].captured_at <= req.audit_at}
    return {rid for rid, obs in index.by_resource.items() if rid.startswith("account_")
            and any(o.file_id in files for o in obs)}


def build_context(requests: list[DeletionRequest], index: EvidenceIndex | None = None,
                  root: Path | None = None) -> AuditContext:
    """root=None reads dataset/; a custom-case directory with the same layout can be passed instead."""
    index = index or (get_index(root) if root else get_index())
    identities, audit_log = load_identities(root), load_audit_log(root)
    lex = build_lexicon(identities, audit_log)
    activity = load_activity(root)
    catalog = parse_catalog_links(activity)
    owner_of = build_ownership(index, lex, catalog)
    ctx = AuditContext(index=index, lexicon=lex, treatments=treatment_by_category(load_rules(root)),
                       activity=activity, owner_of=owner_of, catalog_links=catalog,
                       identities=identities, audit_log=audit_log)
    for res in index.results.values():
        for c in res.coverage:
            ctx.coverage_by_system.setdefault(c.system_id, []).append(c)
    # Workflow attribution is global: every request competes for the same activity stream.
    user_of_case, hints = {}, {}
    for r in requests:
        try:
            ident = resolve_identity(r.email, r.audit_at, identities, audit_log)
        except UnresolvedIdentity:
            user_of_case[r.case_id] = ""
            continue
        user_of_case[r.case_id] = ident.user_id
        hints[r.case_id] = _accounts_seen_with_subject(r, ident, index)
    # Rule-level intent only (deterministic, no LLM): AMBIGUOUS requests still compete like deletion requests.
    non_deletion = {r.case_id for r in requests if classify_intent(r.request_text).intent is Intent.NOT_DELETION}
    ctx.workflow = attribute_workflow(requests, activity, owner_of, user_of_case, hints, non_deletion)
    return ctx


def all_requests() -> list[DeletionRequest]:
    return list(load_sample_requests()) + list(load_requests())


def _effective_category(rid: str, state: ResourceState, observations: list[Observation]) -> str:
    if state.category:
        return state.category
    prefix = rid[: rid.rindex("_") + 1]
    if prefix in PREFIX_CATEGORY:
        return PREFIX_CATEGORY[prefix]
    return "UNCLASSIFIED"  # no rule applies: the request authorizes deletion, nothing requires retention


def latest_signal(observations: list[Observation], files, audit_at, judge) -> tuple[bool | None, Observation | None]:
    """The latest eligible observation that actually states a flow/restore setting (judge() is not None).
    A later observation that is silent about it (e.g. a screenshot row) neither overrides it nor proves anything."""
    for o in sorted((o for o in observations if o.present and eligible(o, files, audit_at)),
                    key=lambda o: (o.observed_at, o.file_id), reverse=True):
        value = judge(o.status_text, o.attrs)
        if value is not None:
            return value, o
    return None, None


def _verdict(category: str, treatment: str, st: ResourceState,
             signal: tuple[bool | None, Observation | None] = (None, None)) -> tuple[Verdict, list[str]]:
    if st.state == "UNKNOWN":
        return Verdict.UNKNOWN, []
    if category in (FLOW_CATEGORY, "BACKUP_ARCHIVE"):
        if st.state == "ABSENT":
            return (Verdict.FLOW_DISABLED_OK if category == FLOW_CATEGORY else Verdict.ABSENT_OK), []
        value = signal[0]
        if value is None:
            # Present and tied to the subject, but no evidence says it is disabled/blocked. Never assumed compliant:
            # rule_14 retains a backup only if restore access CAN be blocked, so the burden of proof is on the block
            # (likewise a flow on its disabling). The finding cites the file showing the record exists, and the
            # planner emits the full finding -> remediation -> verification chain. 0 cases in this dataset (traced).
            return (Verdict.ACTIVE_FLOW if category == FLOW_CATEGORY else Verdict.RESTORE_RISK), ["setting_unproven"]
        if category == FLOW_CATEGORY:
            return (Verdict.ACTIVE_FLOW if value else Verdict.FLOW_DISABLED_OK), []
        return (Verdict.RESTORE_RISK if value else Verdict.RESTORE_BLOCKED_OK), []
    if st.state == "ABSENT":
        return Verdict.ABSENT_OK, []
    if treatment == "RETAIN":
        return Verdict.RETAINED_OK, []
    if treatment in ("REDACT", "ANONYMIZE"):
        exposed = exposed_identifiers(treatment, st.identifiers)
        if st.status_text in ("IDENTIFIABLE",):
            exposed = exposed or ["identifiable"]
        if st.status_text in ("REDACTED", "ANONYMIZED"):
            exposed = []
        if exposed:
            return Verdict.EXPOSED, exposed
        return (Verdict.REDACTED_OK if treatment == "REDACT" else Verdict.ANONYMIZED_OK), []
    return Verdict.REMAINING, []   # DELETE (or no rule): present means it remains


def _select(items: list[Item], workflow_accounts: set[str]) -> list[Item]:
    """Keep what the audit can act on:
      * a resource with no eligible evidence of state is dropped, except the subject's primary account (it is
        always verified; INCOMPLETE cases verify it as future work);
      * one primary account: the account the deletion workflow processed for this request, else the most
        recently observed linked account. Every account that is a violation (REMAINING, EXPOSED, ...) is kept.
    """
    accounts = [i for i in items if i.resource.system_id == "account_service"]
    others = [i for i in items if i.resource.system_id != "account_service" and i.verdict is not Verdict.UNKNOWN]
    violating = [i for i in accounts if i.verdict in VIOLATIONS]
    rest = [i for i in accounts if i.verdict not in VIOLATIONS]
    primary = ([i for i in rest if i.resource.resource_id in workflow_accounts]
               or sorted(rest, key=lambda i: (i.resource.as_of is not None, i.resource.as_of or 0), reverse=True))
    keep = violating + ([] if violating else primary[:1])
    return sorted(keep + others, key=lambda i: i.resource.resource_id)


RETAIN_CATEGORIES = {"SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"}
INTENT_DECISIONS_PATH = DATA_DIR / "intent_decisions.json"


def _late_retained(req: DeletionRequest, ident: Identity, ctx: AuditContext, have: set[str]) -> list[Item]:
    """Records policy requires to RETAIN, seen at or before audit_at but only in a file exported later.

    Such a file never sets state or gets cited (C3). It may only add a future VERIFY_RECORD_PRESENT check to an
    INCOMPLETE plan (no citation, no effect on status): the record existed before audit_at, and the remediation
    must not destroy it. The samples confirm this (case_09/23/24/29/30)."""
    out = []
    for rid, obs in ctx.index.by_resource.items():
        if rid in have:
            continue
        late = [o for o in obs if o.observed_at and o.observed_at <= req.audit_at
                and ctx.index.files[o.file_id].captured_at > req.audit_at
                and o.data_category in RETAIN_CATEGORIES and o.present
                and (any(ident.matches(k, v) for k, v in o.identifiers.items())
                     or (o.lookup_key and ident.matches_any(o.lookup_key)))]
        if late:
            o = late[0]
            st = ResourceState(system_id=o.system_id, resource_id=rid, category=o.data_category, state="PRESENT",
                               as_of=o.observed_at, evidence_id="", basis="late-file")
            out.append(Item(resource=st, category=o.data_category, treatment="RETAIN", verdict=Verdict.RETAINED_OK,
                            link="late-file", link_detail=f"{o.file_id} (exported after audit_at; not cited)"))
    return out


# Names are weak identifiers (shared by different people; the dataset contains decoys). Review 2 (#6):
#   "any"    : a name alone links (behaviour before the fix)
#   "unique" : a name alone links only if exactly one known identity carries it
#   "strict" : a name never links on its own; it can only accompany a strong identifier
NAME_LINK_POLICY = "strict"
STRONG_KINDS = ("email", "user_ref", "email_hash", "phone")


def _identifier_hit(ident: Identity, o: Observation, lex: Lexicon) -> str | None:
    """Why this observation belongs to the subject, or None. Strong identifiers first; a name only per policy."""
    for k, v in sorted(o.identifiers.items()):
        if k in STRONG_KINDS and ident.matches(k, v):
            return f"{k}={v}"
    if o.lookup_key and any(ident.matches(k, o.lookup_key) for k in STRONG_KINDS):
        return f"lookup_key={o.lookup_key}"
    names = [v for k, v in o.identifiers.items() if k == "name" and ident.matches("name", v)]
    if o.lookup_key and ident.matches("name", o.lookup_key):
        names.append(o.lookup_key)
    for n in names:
        if NAME_LINK_POLICY == "any" or (NAME_LINK_POLICY == "unique" and len(_users_for("name", n, lex)) == 1):
            return f"name={n}"
    return None


def assess_case(req: DeletionRequest, ctx: AuditContext) -> CaseAssessment:
    intent = classify_intent(req.request_text)
    rule_intent = intent.intent.value
    if intent.intent is Intent.AMBIGUOUS:
        stored = load_decisions(INTENT_DECISIONS_PATH).get(decision_key(req.request_text))
        if stored is not None:  # a decision made earlier (possibly on another machine): no LLM needed
            intent = from_decision(stored)
        else:
            if ctx._intent_extractor is None and ctx.intent_extractor_factory is not None:
                ctx._intent_extractor = ctx.intent_extractor_factory()
            if ctx._intent_extractor is not None:
                intent = classify_with_llm(req.request_text, ctx._intent_extractor, INTENT_DECISIONS_PATH)
            else:
                intent = IntentResult(intent=Intent.NOT_DELETION, reason="FALLBACK: ambiguous and no LLM "
                                      "decision available; no deletion authorized", source="fallback")
    if intent.intent is not Intent.DELETION:
        return CaseAssessment(case_id=req.case_id, status="NOT_APPLICABLE", intent=intent, rule_intent=rule_intent)
    try:
        ident = resolve_identity(req.email, req.audit_at, ctx.identities, ctx.audit_log)
    except UnresolvedIdentity as exc:
        # A state, not a crash (hard rule 9) -- but never a claim: status ERROR is not a valid output status, so
        # run_all refuses to write output.csv and lists the case (no COMPLETE/INCOMPLETE without proof).
        return CaseAssessment(case_id=req.case_id, status="ERROR", intent=intent, rule_intent=rule_intent,
                              notes=[f"IDENTITY_UNRESOLVED: {exc}"])
    files = ctx.index.files

    links: dict[str, tuple[str, str]] = {}
    for rid, obs in ctx.index.by_resource.items():
        for o in obs:
            if not eligible(o, files, req.audit_at):
                continue
            hit = _identifier_hit(ident, o, ctx.lexicon)
            if hit:
                links.setdefault(rid, ("strong", f"{o.file_id}: {hit}"))
            elif not o.identifiers and o.attrs.get("page_user_ref") == ident.user_id:
                links.setdefault(rid, ("page", f"{o.file_id}: page about {ident.user_id}"))
    for rid, cat_links in ctx.catalog_links.items():
        for a, key, uid in cat_links:
            # the catalog names its own user_id: trust that assertion, never a key match to a different user
            if a.event_time <= req.audit_at and uid == ident.user_id:
                links[rid] = ("strong", f"{a.activity_id}: catalog link to {uid}")
    workflow_accounts = set()
    for wl in ctx.workflow.get(req.case_id, []):
        links.setdefault(wl.activity.resource_id, ("workflow", f"{wl.activity.activity_id} ({wl.confidence})"))
        if wl.activity.resource_id.startswith("account_"):
            workflow_accounts.add(wl.activity.resource_id)

    # A subject-scoped extract only proves absence for the subjects it mentions.
    subj = subject_keys(ident.emails + [ident.user_id] + ident.email_hashes + ident.phones + ident.names)
    applies = lambda c: (not c.scoped) or bool(subj & ctx.index.file_subjects.get(c.file_id, set()))  # noqa: E731

    items = []
    for rid in sorted(links):
        obs = ctx.index.by_resource.get(rid, [])
        # Known only from activity (no observation): its system comes from the activity, and coverage can
        # still prove absence (e.g. account_001 missing from evidence_54's full account tables).
        sys_ = obs[0].system_id if obs else next((a.target_system for a in ctx.activity if a.resource_id == rid),
                                                 "unknown")
        st = state_at(rid, obs, ctx.coverage_by_system.get(sys_, []), files, req.audit_at, sys_, applies)
        category = _effective_category(rid, st, obs)
        treatment = "DISABLE" if category == FLOW_CATEGORY else ctx.treatments.get(category, "DELETE")
        signal: tuple[bool | None, Observation | None] = (None, None)
        if category in (FLOW_CATEGORY, "BACKUP_ARCHIVE") and st.state == "PRESENT":
            signal = latest_signal(obs, files, req.audit_at,
                                   flow_active if category == FLOW_CATEGORY else restore_open)
            if signal[1] is not None and signal[1].file_id != st.evidence_id:
                # cite the file that states the setting, not a later one that is silent about it
                st = st.model_copy(update={"evidence_id": signal[1].file_id, "as_of": signal[1].observed_at})
        verdict, exposed = _verdict(category, treatment, st, signal)
        items.append(Item(resource=st, category=category, treatment=treatment, verdict=verdict,
                          link=links[rid][0], link_detail=links[rid][1], exposed=exposed))
    unresolved = [i for i in items if i.verdict is Verdict.UNKNOWN and i.resource.system_id != "account_service"]
    items = _select(items, workflow_accounts)
    status = "INCOMPLETE" if any(i.verdict in VIOLATIONS for i in items) else "COMPLETE"
    if status == "INCOMPLETE":
        items = sorted(items + _late_retained(req, ident, ctx, {i.resource.resource_id for i in items}),
                       key=lambda i: i.resource.resource_id)
    return CaseAssessment(case_id=req.case_id, status=status, intent=intent, rule_intent=rule_intent,
                          identity=ident, items=items, unresolved=unresolved)
