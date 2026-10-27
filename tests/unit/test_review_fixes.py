"""Regression tests for the 2026-10-24 code review (docs/code-review-2026-10-24.md). One test per finding; each
fails on the code as it was before the fix."""
import csv
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.audit.identity import email_hash, resolve_identity
from app.audit.intent import Intent, IntentResult, classify_intent
from app.audit.planner import plan_steps
from app.audit.status import CaseAssessment, Item, Verdict, _select, _verdict, latest_signal
from app.audit.flows import flow_active, restore_open
from app.audit.timeline import ResourceState
from app.audit.workflow import attribute_workflow
from app.eval.validate_output import HEADER, validate_rows
from app.schemas import (AuditLogEntry, DeletionRequest, EvidenceFile, Observation, SystemActivity, UserIdentity)

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
AUDIT = T0 + timedelta(days=10)
INTENT = IntentResult(intent=Intent.DELETION, reason="t")


def at(d):
    return T0 + timedelta(days=d)


def item(system, rid, verdict, ev="evidence_1", treatment="DELETE", state="PRESENT"):
    st = ResourceState(system_id=system, resource_id=rid, category="X", state=state, as_of=at(1), evidence_id=ev)
    return Item(resource=st, category="X", treatment=treatment, verdict=verdict, link="strong", link_detail="t")


# ---- #1 an unresolvable identity is a state, not a crash
def test_1_unresolved_identity_returns_a_state(tmp_path):
    from app.audit.status import assess_case, build_context
    root = tmp_path
    (root / "evidence").mkdir()
    def w(name, header, rows):
        with (root / name).open("w", encoding="utf-8", newline="") as fh:
            c = csv.writer(fh, lineterminator="\n"); c.writerow(header); c.writerows(rows)
    w("deletion_requests.csv", ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at",
                                "request_text"],
      [["case_x", "nobody@example.org", "2026-09-01T00:00:00Z", "COMPLETE", "2026-09-02T00:00:00Z",
        "2026-09-10T00:00:00Z", "Please delete my account."]])
    w("user_identities.csv", ["user_id", "name", "email", "phone", "email_hash", "updated_at"],
      [["user_1", "Someone Else", "else@example.org", "+1 555 000 0000", email_hash("else@example.org"),
        "2026-01-01T00:00:00Z"]])
    w("user_audit_log.csv", ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"], [])
    w("system_activity.csv", ["activity_id", "event_time", "event_type", "source_system", "target_system",
                              "resource_id", "reported_result", "details"], [])
    w("evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"], [])
    from app.config import DATASET_DIR
    (root / "deletion_rules.csv").write_text((DATASET_DIR / "deletion_rules.csv").read_text(encoding="utf-8"),
                                             encoding="utf-8")
    from app.ingest.loaders import load_requests
    reqs = list(load_requests(root))
    a = assess_case(reqs[0], build_context(reqs, root=root))
    # review 2 (#1/#2): never COMPLETE without proof; ERROR is not a valid output status, so the run aborts
    assert a.status == "ERROR" and plan_steps(a) == [] and a.notes[0].startswith("IDENTITY_UNRESOLVED")
    assert validate_rows(HEADER, [{"case_id": "case_x", "deletion_status": "ERROR", "steps_json": "[]"}],
                         ["case_x"])[0]


# ---- #2 an account that is a violation is never dropped by the primary-account rule
def test_2_exposed_account_is_kept_next_to_primary():
    items = [item("account_service", "account_1", Verdict.ABSENT_OK),
             item("account_service", "account_2", Verdict.EXPOSED, treatment="REDACT")]
    kept = {i.resource.resource_id for i in _select(items, workflow_accounts={"account_1"})}
    assert "account_2" in kept


# ---- #3 COMPLETE never emits an uncited verification
def test_3_complete_plan_skips_uncited_records():
    a = CaseAssessment(case_id="c", status="COMPLETE", intent=INTENT,
                       items=[item("account_service", "account_1", Verdict.UNKNOWN, ev="", state="UNKNOWN"),
                              item("security_service", "audit_record_1", Verdict.RETAINED_OK, treatment="RETAIN")])
    steps = plan_steps(a)
    assert [s["resource_id"] for s in steps] == ["audit_record_1"] and all(s["evidence_id"] for s in steps)


# ---- #4 a flow/backup with no stated setting is UNKNOWN, and a silent later observation does not override one
def test_4_unknown_setting_is_not_compliance_and_latest_signal_is_used():
    st = ResourceState(system_id="integration_service", resource_id="data_flow_1", category="DATA_FLOW",
                       state="PRESENT", as_of=at(5), evidence_id="evidence_2")
    # review 2 (#3): an unstated setting is never compliance; it is a violation with the full chain
    assert _verdict("DATA_FLOW", "DISABLE", st, (None, None))[0] is Verdict.ACTIVE_FLOW
    assert _verdict("BACKUP_ARCHIVE", "RETAIN", st, (None, None))[0] is Verdict.RESTORE_RISK
    risk = CaseAssessment(case_id="c", status="INCOMPLETE", intent=INTENT,
                          items=[item("backup_service", "backup_1", Verdict.RESTORE_RISK, ev="evidence_2")])
    assert [(s["operation"], s["evidence_id"]) for s in plan_steps(risk)] == [
        ("REPORT_RESTORE_RISK", "evidence_2"), ("BLOCK_RESTORE", ""), ("VERIFY_RESTORE_BLOCKED", "")]
    files = {f: EvidenceFile(file_id=f, file_path=f, origin="o", captured_at=at(d)) for f, d in
             (("evidence_1", 2), ("evidence_2", 6))}
    obs = [Observation(file_id="evidence_1", locator="l", source_kind="json", observed_at=at(1),
                       system_id="integration_service", resource_id="data_flow_1", present=True,
                       attrs={"enabled": True}),
           Observation(file_id="evidence_2", locator="row", source_kind="ocr", observed_at=at(5),   # silent screen
                       system_id="integration_service", resource_id="data_flow_1", present=True, status_text="shown")]
    value, src = latest_signal(obs, files, AUDIT, flow_active)
    assert value is True and src.file_id == "evidence_1"
    assert latest_signal(obs[1:], files, AUDIT, restore_open) == (None, None)


# ---- #5 a non-deletion request cannot take a deletion request's workflow events
def test_5_non_deletion_request_does_not_compete():
    def req(cid, status):
        return DeletionRequest(case_id=cid, email=f"{cid}@x.org", requested_at=at(0), reported_status=status,
                               reported_at=at(1), audit_at=AUDIT, request_text="t")
    reqs = [req("del", "COMPLETE"), req("na", "COMPLETE")]
    acts = (SystemActivity(activity_id="a1", event_time=at(1) - timedelta(minutes=50), event_type="DELETE_RECORD",
                           source_system="deletion_worker", target_system="account_service", resource_id="account_9",
                           reported_result="SUCCESS", details="d"),
            SystemActivity(activity_id="a2", event_time=at(1) - timedelta(minutes=30), event_type="ANONYMIZE_RECORD",
                           source_system="deletion_worker", target_system="analytics_platform",
                           resource_id="analytics_9", reported_result="SUCCESS", details="d"))
    links = attribute_workflow(reqs, acts, {}, {"del": "u1", "na": "u2"}, non_deletion={"na"})
    assert {l.activity.resource_id for l in links["del"]} == {"account_9", "analytics_9"}
    assert "na" not in links


# ---- #6 / #7 intent edge cases
@pytest.mark.parametrize("text,expected", [
    ("I still want my account deleted.", Intent.DELETION),
    ("You still use my email, please erase it.", Intent.DELETION),
    ("Please remove my email from your marketing lists.", Intent.NOT_DELETION),
    ("Please remove me from marketing lists only. I still want receipts and service messages.", Intent.NOT_DELETION),
    ("Please stop the promotional emails. I still need the account and my order history.", Intent.NOT_DELETION),
])
def test_6_7_intent_edge_cases(text, expected):
    assert classify_intent(text).intent is expected


# ---- #8 the run counts AMBIGUOUS from the rule-level intent (before any LLM/fallback)
def test_8_rule_intent_survives_fallback():
    a = CaseAssessment(case_id="c", status="NOT_APPLICABLE", intent=IntentResult(intent=Intent.NOT_DELETION,
                       reason="fallback"), rule_intent=Intent.AMBIGUOUS.value)
    assert a.rule_intent == "AMBIGUOUS"


# ---- #9 a hash of an email introduced after audit_at is not an identifier at audit_at
def test_9_post_audit_email_hash_is_excluded():
    users = (UserIdentity(user_id="user_1", name="Ana", email="new@x.org", phone="+1 555 0101 000",
                          email_hash=email_hash("new@x.org"), updated_at=at(20)),)
    log = (AuditLogEntry(audit_id="audit_1", user_id="user_1", event_time=at(20), field_name="email",
                         old_value="old@x.org", new_value="new@x.org"),)   # no email_hash row on purpose
    i = resolve_identity("old@x.org", AUDIT, users, log)
    assert i.emails == ["old@x.org"]
    assert email_hash("new@x.org") not in i.email_hashes and email_hash("old@x.org") in i.email_hashes


# ---- minor: a short CSV row is reported, not a crash
def test_minor_validator_reports_short_rows():
    errors, _ = validate_rows(HEADER, [{"case_id": "case_1", "deletion_status": "COMPLETE", "steps_json": None}],
                              ["case_1"])
    assert any("missing steps_json" in e for e in errors)


# ---- #4 follow-up: a screenshot row states the setting in words (only the record's own row/card)
def test_4b_screenshot_text_states_flow_and_restore_settings():
    shown = lambda text: ("shown", {"group_text": text})  # noqa: E731
    assert flow_active(*shown("data_flow_1121 | Healthy | Sync mode | Continuous sync")) is True
    assert flow_active(*shown("data_flow_7 | Paused | Sync mode | Continuous")) is False      # negative wins
    assert flow_active(*shown("data_flow_7 | customer_analytics_warehouse")) is None          # not stated
    assert restore_open(*shown("backup_9287 | areed@x.org | 84.7 GB | AES-256 | Archive")) is True
    assert restore_open(*shown("backup_1 | Archive | Restore blocked")) is False
    assert flow_active(None, {"group_text": "Continuous"}) is None   # words count only on screenshot rows


# ---- review 2: #5 negation-aware screen keywords, #7 scoped limitation markers (one parameterized test each)
@pytest.mark.parametrize("fn,text,expected", [
    (restore_open, "backup_1 | archive | restore unblocked", True),
    (restore_open, "backup_1 | unlocked archive", True),
    (restore_open, "backup_1 | no legal hold | archive", True),
    (restore_open, "backup_1 | archive | restore blocked", False),
    (flow_active, "data_flow_1 | not stopped | continuous", True),
    (flow_active, "data_flow_2 | paused", False),
])
def test_r2_5_screen_keywords_whole_word_and_negation(fn, text, expected):
    assert fn("shown", {"group_text": text}) is expected


@pytest.mark.parametrize("text,expected", [
    ("I no longer want you to keep the account, please delete everything.", Intent.DELETION),
    ("Do not delete my invoices, but erase my profile.", Intent.DELETION),
    ("Do not close the account.", Intent.NOT_DELETION),
    ("Please keep the account as it is.", Intent.NOT_DELETION),
])
def test_r2_7_limitation_markers_are_scoped(text, expected):
    assert classify_intent(text).intent is expected


def test_r2_4_deletion_block_false_alone_means_restore_is_open():
    assert restore_open(None, {"deletion_block": "false"}) is True
    assert restore_open(None, {"deletion_block": "true"}) is False
    assert restore_open(None, {"eligible": "false"}) is False


# ---- review 2: #8 stored LLM intent decisions are reproduced without an LLM; #9 summary lines are not calls
def test_r2_8_stored_decision_is_used_without_llm(tmp_path):
    from app.audit.intent import from_decision, load_decisions, save_decision, decision_key
    path = tmp_path / "intent_decisions.json"
    text = "Regarding my profile, please handle it as discussed."
    save_decision(path, text, {"authorizes_deletion": True, "request_type": "deletion", "quote": "handle it"})
    stored = load_decisions(path)[decision_key(text)]
    assert from_decision(stored).intent is Intent.DELETION and from_decision(stored).source == "llm"


def test_r2_9_extraction_failed_summary_is_not_a_call(tmp_path):
    from app.llm.cache import LLMCache
    from app.llm.client import FakeLLMClient, StructuredExtractor
    from app.llm.ledger import Ledger, read_ledger
    from pydantic import BaseModel
    from scripts.usage_report import summarize

    class Thing(BaseModel):
        n: int
    led = Ledger(tmp_path / "l.jsonl", run_id="t")
    StructuredExtractor(FakeLLMClient(["x", "y", "z"]), LLMCache(tmp_path / "c"), led, num_ctx=4096).extract(
        Thing, "s", "p", [], ["h"], "t", "f", "v1", 64)
    entries = read_ledger(tmp_path / "l.jsonl")
    assert [e.summary for e in entries] == [False, False, False, True]
    assert summarize(entries)["calls"] == 3          # 3 real attempts, not 4


# ---- review 2 (#6): a name alone never links a record to the subject (decoys share names); catalog uid is trusted
def test_r2_6_name_alone_does_not_link():
    from app.audit.status import _identifier_hit
    from app.extract.lexicon import build_lexicon
    users = (UserIdentity(user_id="user_1", name="Maria Garcia", email="maria@x.org", phone="+34 600 000 001",
                          email_hash=email_hash("maria@x.org"), updated_at=at(-5)),
             UserIdentity(user_id="user_2", name="Maria Garcia", email="other@x.org", phone="+34 600 000 002",
                          email_hash=email_hash("other@x.org"), updated_at=at(-5)))
    lex = build_lexicon(users, ())
    ident = resolve_identity("maria@x.org", AUDIT, users, ())
    by_name = Observation(file_id="e", locator="l", source_kind="ocr", observed_at=at(1), system_id="support_platform",
                          resource_id="support_1", present=True, identifiers={"name": "mariagarcia"})
    by_email = by_name.model_copy(update={"identifiers": {"name": "mariagarcia", "email": "maria@x.org"}})
    assert _identifier_hit(ident, by_name, lex) is None
    assert _identifier_hit(ident, by_email, lex) == "email=maria@x.org"
