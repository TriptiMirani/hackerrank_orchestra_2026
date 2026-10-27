"""Audit core on small synthetic inputs: identity, timeline cutoff, drift, flows, restore, tombstone, intent."""
from datetime import datetime, timedelta, timezone

from app.audit.flows import flow_active, restore_open
from app.audit.identity import email_hash, resolve_identity
from app.audit.intent import Intent, classify_intent
from app.audit.rules import exposed_identifiers
from app.audit.status import Verdict, _verdict
from app.audit.timeline import state_at
from app.audit.workflow import attribute_workflow
from app.schemas import (AuditLogEntry, DeletionRequest, EvidenceFile, Observation, ScopeCoverage, SystemActivity,
                         UserIdentity)

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
AUDIT = T0 + timedelta(days=10)


def at(days: float) -> datetime:
    return T0 + timedelta(days=days)


def files(**captured) -> dict[str, EvidenceFile]:
    return {fid: EvidenceFile(file_id=fid, file_path=f"x/{fid}", origin="o", captured_at=ts) for fid, ts in captured.items()}


def obs(fid, when, present, rid="account_1", system="account_service", topology=None, **ids):
    attrs = {"topology": topology} if topology else {}
    return Observation(file_id=fid, locator="l", source_kind="json", observed_at=when, system_id=system,
                       resource_id=rid, present=present, data_category="USER_PROFILE", identifiers=ids, attrs=attrs)


# ---------------------------------------------------------------- identity
def ident_data():
    users = (UserIdentity(user_id="user_7", name="Ana Silva", email="ana.new@x.com", phone="+1 555 0100 200",
                          email_hash=email_hash("ana.new@x.com"), updated_at=at(-30)),)
    log = (AuditLogEntry(audit_id="audit_1", user_id="user_7", event_time=at(-30), field_name="email",
                         old_value="ana.old@x.com", new_value="ana.new@x.com"),
           AuditLogEntry(audit_id="audit_2", user_id="user_7", event_time=at(-30), field_name="email_hash",
                         old_value=email_hash("ana.old@x.com"), new_value=email_hash("ana.new@x.com")))
    return users, log


def test_identity_resolves_through_a_historical_email():
    users, log = ident_data()
    i = resolve_identity("ANA.OLD@x.com", AUDIT, users, log)
    assert i.user_id == "user_7" and i.matched_via == "historical_email"
    assert set(i.emails) == {"ana.new@x.com", "ana.old@x.com"}
    assert email_hash("ana.old@x.com") in i.email_hashes and i.matches("email_hash", email_hash("ana.new@x.com"))
    assert i.matches("phone", "+1-555-0100-200") and i.matches("name", "AnaSilva")


def test_identity_excludes_values_introduced_after_audit_at():
    users, log = ident_data()
    i = resolve_identity("ana.old@x.com", at(-40), users, log)   # audit before the email change
    assert i.emails == ["ana.old@x.com"]


# ---------------------------------------------------------------- timeline
def test_timeline_ignores_files_captured_after_audit_at():
    fs = files(f_early=at(2), f_late=at(12))
    o = [obs("f_early", at(1), False), obs("f_late", at(9), True)]   # late FILE, early inner time
    st = state_at("account_1", o, [], fs, AUDIT)
    assert st.state == "ABSENT" and st.evidence_id == "f_early"


def test_sync_after_deletion_recreates_data():
    fs = files(f1=at(2), f2=at(6))
    o = [obs("f1", at(1), False), obs("f2", at(5), True, email="ana.new@x.com")]  # 404, then back after a sync
    st = state_at("account_1", o, [], fs, AUDIT)
    assert st.state == "PRESENT" and st.evidence_id == "f2"
    assert _verdict("USER_PROFILE", "DELETE", st)[0] is Verdict.REMAINING      # -> INCOMPLETE


def test_replica_copy_survives_primary_absence():
    fs = files(snap=at(2), inv=at(4))
    o = [obs("snap", at(2), True, topology="failover_replica")]
    cov = [ScopeCoverage(file_id="snap", system_id="account_service", collection="customer_profiles_primary",
                         captured_at=at(2), record_count=0, scoped=False),
           ScopeCoverage(file_id="inv", system_id="account_service", collection="customer_profiles",
                         captured_at=at(4), record_count=0, scoped=False)]
    st = state_at("account_1", o, cov, fs, AUDIT)
    assert st.state == "PRESENT" and st.evidence_id == "snap"   # live copy gone, replica copy remains


def test_absence_by_coverage_respects_subject_scope():
    fs = files(bundle=at(3))
    cov = [ScopeCoverage(file_id="bundle", system_id="account_service", collection="customer_profiles@snapshots[0]",
                         captured_at=at(3), record_count=0, scoped=True)]
    assert state_at("account_1", [], cov, fs, AUDIT, "account_service", applies=lambda c: True).state == "ABSENT"
    assert state_at("account_1", [], cov, fs, AUDIT, "account_service", applies=lambda c: False).state == "UNKNOWN"


# ---------------------------------------------------------------- flows, restore, tombstone, retained
def test_restore_path_is_a_risk_and_blocked_is_compliant():
    assert restore_open(None, {"restore_allowed": True}) is True
    assert restore_open(None, {"eligible": "true", "deletion_block": "false"}) is True
    assert restore_open("BLOCKED", {}) is False and restore_open(None, {"decision": "deny"}) is False
    fs = files(f=at(1))
    st = state_at("backup_1", [obs("f", at(1), True, rid="backup_1", system="backup_service")], [], fs, AUDIT)
    assert _verdict("BACKUP_ARCHIVE", "RETAIN", st, (True, None))[0] is Verdict.RESTORE_RISK
    assert _verdict("BACKUP_ARCHIVE", "RETAIN", st, (False, None))[0] is Verdict.RESTORE_BLOCKED_OK


def test_active_flow():
    assert flow_active(None, {"enabled": "true"}) is True and flow_active("DISABLED", {}) is False


def test_tombstone_keeps_user_id_but_drops_direct_identifiers():
    assert exposed_identifiers("REDACT", {"user_ref": "user_7"}) == []          # tombstone rule_15: REDACT
    assert exposed_identifiers("REDACT", {"user_ref": "user_7", "email": "a@x.com", "email_hash": "h",
                                          "name": "n", "phone": "p"}) == ["email", "email_hash", "name", "phone"]
    # ANONYMIZE (analytics) must be unlinkable: even the internal reference counts
    assert exposed_identifiers("ANONYMIZE", {"user_ref": "user_7"}) == ["user_ref"]


def test_retained_record_is_verified_present():
    fs = files(f=at(1))
    st = state_at("audit_record_1", [obs("f", at(1), True, rid="audit_record_1", system="security_service")], [],
                  fs, AUDIT)
    assert _verdict("SECURITY_AUDIT_LOG", "RETAIN", st)[0] is Verdict.RETAINED_OK


# ---------------------------------------------------------------- intent
def test_non_deletion_requests_are_not_applicable():
    for text in ["Can you tell me what personal information you hold about me? This is an information request, "
                 "not a request to erase it.",
                 "Could you send me a copy of the information held on my account? I still use it, so please don't "
                 "delete or change anything.",
                 "Please remove me from marketing lists only."]:
        assert classify_intent(text).intent is Intent.NOT_DELETION, text
    assert classify_intent("I closed my account, but an old 'download your data' link still works. "
                           "Please delete that export file as well.").intent is Intent.DELETION
    assert classify_intent("Je demande la suppression définitive du compte.").intent is Intent.DELETION


# ---------------------------------------------------------------- workflow attribution
def test_workflow_attribution_propagates_and_respects_ownership():
    reqs = [DeletionRequest(case_id=c, email=f"{c}@x.com", requested_at=at(0), reported_status=s, reported_at=at(1),
                            audit_at=AUDIT, request_text="delete") for c, s in (("c1", "COMPLETE"), ("c2", "NOT_STARTED"))]
    act = tuple(SystemActivity(activity_id=f"a{i}", event_time=at(1) - timedelta(minutes=50), event_type="DELETE_RECORD",
                               source_system="deletion_worker", target_system="account_service", resource_id=rid,
                               reported_result=res, details="d")
                for i, (rid, res) in enumerate((("account_1", "SUCCESS"), ("account_2", "FAILED"))))
    links = attribute_workflow(reqs, act, owner_of={}, user_of_case={"c1": "u1", "c2": "u2"})
    assert links["c1"][0].activity.resource_id == "account_1" and links["c2"][0].activity.resource_id == "account_2"
    links = attribute_workflow(reqs, act, owner_of={"account_1": "u2"}, user_of_case={"c1": "u1", "c2": "u2"})
    assert all(l.activity.resource_id != "account_1" for l in links.get("c1", []))   # never another user's record


# ---------------------------------------------------------------- LLM intent fallback (fake client only)
def test_ambiguous_intent_goes_to_llm_and_failure_means_no_deletion(tmp_path):
    from app.audit.intent import classify_with_llm
    from app.llm.cache import LLMCache
    from app.llm.client import FakeLLMClient, LLMStatus, LLMTransportError, StructuredExtractor
    from app.llm.ledger import Ledger

    text = "Hello, regarding my profile, please handle it as discussed."
    assert classify_intent(text).intent is Intent.AMBIGUOUS
    ok = StructuredExtractor(FakeLLMClient(['{"authorizes_deletion": true, "request_type": "deletion", '
                                            '"quote": "handle it"}']),
                             LLMCache(tmp_path / "c"), Ledger(tmp_path / "l.jsonl", run_id="t"), num_ctx=4096)
    r = classify_with_llm(text, ok)
    assert r.intent is Intent.DELETION and r.source == "llm"
    down = StructuredExtractor(FakeLLMClient([LLMTransportError(LLMStatus.UNREACHABLE, "down")]),
                               LLMCache(tmp_path / "c2"), Ledger(tmp_path / "l2.jsonl", run_id="t"), num_ctx=4096)
    assert classify_with_llm(text, down).intent is Intent.NOT_DELETION
