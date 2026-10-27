"""Planner ordering/citation rules, the scorer, and validate_output rejections."""
import json
from datetime import datetime, timezone

from app.audit.intent import Intent, IntentResult
from app.audit.planner import plan_steps, to_steps_json
from app.audit.status import CaseAssessment, Item, Verdict
from app.audit.timeline import ResourceState
from app.eval.scorer import score
from app.eval.validate_output import HEADER, validate_rows

T = datetime(2026, 9, 1, tzinfo=timezone.utc)
INTENT = IntentResult(intent=Intent.DELETION, reason="test")


def item(system, rid, verdict, treatment="DELETE", ev="evidence_9", category="X"):
    st = ResourceState(system_id=system, resource_id=rid, category=category, state="PRESENT", as_of=T, evidence_id=ev)
    return Item(resource=st, category=category, treatment=treatment, verdict=verdict, link="strong", link_detail="t")


def test_incomplete_plan_order_and_citations():
    a = CaseAssessment(case_id="c", status="INCOMPLETE", intent=INTENT, items=[
        item("support_platform", "support_864", Verdict.EXPOSED, "REDACT"),
        item("analytics_platform", "analytics_2034", Verdict.EXPOSED, "ANONYMIZE"),
        item("search_service", "search_052", Verdict.REMAINING),
        item("integration_service", "data_flow_9522", Verdict.ACTIVE_FLOW, "DISABLE"),
        item("security_service", "audit_record_9394", Verdict.RETAINED_OK, "RETAIN"),
        item("account_service", "account_4248", Verdict.ABSENT_OK),
    ])
    ops = [(s["step_no"], s["operation"], s["resource_id"], s["evidence_id"]) for s in plan_steps(a)]
    # exactly sample case_11's structure
    assert [o[1:3] for o in ops] == [
        ("REPORT_REMAINING_RECORD", "search_052"), ("REPORT_EXPOSED_PII", "analytics_2034"),
        ("REPORT_EXPOSED_PII", "support_864"), ("REPORT_ACTIVE_DATA_FLOW", "data_flow_9522"),
        ("DISABLE_DATA_FLOW", "data_flow_9522"), ("DELETE_RECORD", "search_052"),
        ("REDACT_RECORD", "support_864"), ("ANONYMIZE_RECORD", "analytics_2034"),
        ("VERIFY_RECORD_ABSENT", "account_4248"), ("VERIFY_RECORD_ABSENT", "search_052"),
        ("VERIFY_RECORD_PRESENT", "audit_record_9394"), ("VERIFY_RECORD_REDACTED", "support_864"),
        ("VERIFY_RECORD_ANONYMIZED", "analytics_2034"), ("VERIFY_DATA_FLOW_DISABLED", "data_flow_9522")]
    assert [o[0] for o in ops] == list(range(1, 15))
    assert all(o[3] == "evidence_9" for o in ops[:4]) and all(o[3] == "" for o in ops[4:])


def test_restore_path_plans_block_and_verify():
    a = CaseAssessment(case_id="c", status="INCOMPLETE", intent=INTENT,
                       items=[item("backup_service", "backup_1", Verdict.RESTORE_RISK, "RETAIN")])
    assert [s["operation"] for s in plan_steps(a)] == ["REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"]


def test_complete_is_verification_only_with_citations_and_na_is_empty():
    a = CaseAssessment(case_id="c", status="COMPLETE", intent=INTENT,
                       items=[item("identity_service", "tombstone_1", Verdict.REDACTED_OK, "REDACT", ev="evidence_51")])
    (s,) = plan_steps(a)
    assert (s["phase"], s["operation"], s["state"], s["evidence_id"]) == ("VERIFICATION", "VERIFY_RECORD_REDACTED",
                                                                          "REDACTED", "evidence_51")
    na = CaseAssessment(case_id="c", status="NOT_APPLICABLE", intent=INTENT)
    assert plan_steps(na) == [] and to_steps_json([]) == "[]"


def test_steps_json_is_byte_stable():
    a = CaseAssessment(case_id="c", status="INCOMPLETE", intent=INTENT,
                       items=[item("search_service", "search_1", Verdict.REMAINING)])
    assert to_steps_json(plan_steps(a)) == to_steps_json(plan_steps(a))
    assert to_steps_json(plan_steps(a)).startswith('[{"step_no":1,"phase":"FINDING","system_id":"search_service"')


def rows_for(ids, steps="[]", status="NOT_APPLICABLE"):
    return [{"case_id": i, "deletion_status": status, "steps_json": steps} for i in ids]


def test_validator_accepts_good_file():
    errors, _ = validate_rows(HEADER, rows_for(["case_2", "case_10"]), ["case_2", "case_10"])
    assert errors == []


def test_validator_rejects_count_order_json_and_illegal_pairs():
    ids = ["case_2", "case_10"]
    assert any("row count" in e for e in validate_rows(HEADER, rows_for(ids[:1]), ids)[0])
    assert any("sorted" in e for e in validate_rows(HEADER, rows_for(ids[::-1]), ids)[0])
    bad_json = rows_for(ids)
    bad_json[0]["steps_json"] = "[{"
    assert any("valid JSON" in e for e in validate_rows(HEADER, bad_json, ids)[0])
    step = {"step_no": 1, "phase": "VERIFICATION", "system_id": "s", "resource_id": "r",
            "operation": "VERIFY_RECORD_ABSENT", "state": "PRESENT", "evidence_id": "evidence_1"}
    illegal = rows_for(ids)
    illegal[0].update(deletion_status="COMPLETE", steps_json=json.dumps([step]))
    assert any("illegal operation/state" in e for e in validate_rows(HEADER, illegal, ids)[0])
    uncited = rows_for(ids)
    uncited[0].update(deletion_status="COMPLETE", steps_json=json.dumps([{**step, "state": "ABSENT", "evidence_id": ""}]))
    assert any("must cite evidence" in e for e in validate_rows(HEADER, uncited, ids)[0])
    na_steps = rows_for(ids)
    na_steps[0]["steps_json"] = json.dumps([{**step, "state": "ABSENT"}])
    assert any("NOT_APPLICABLE" in e for e in validate_rows(HEADER, na_steps, ids)[0])


def test_scorer_metrics():
    s1 = {"step_no": 1, "phase": "VERIFICATION", "system_id": "a", "resource_id": "account_1",
          "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": "e1"}
    s2 = {**s1, "step_no": 2, "system_id": "b", "resource_id": "audit_1", "operation": "VERIFY_RECORD_PRESENT",
          "state": "PRESENT"}
    exp = {"c1": ("COMPLETE", [s1, s2]), "c2": ("NOT_APPLICABLE", [])}
    pred = {"c1": ("COMPLETE", [{**s1, "resource_id": "account_9"}, s2]), "c2": ("NOT_APPLICABLE", [])}
    r = score(exp, pred)
    assert r["status_accuracy"] == 1.0 and r["steps_matched"] == 1 and r["step_f1"] == 0.5
    assert r["field_accuracy"]["resource_id"] == 0.5 and r["field_accuracy"]["operation"] == 1.0
    assert r["sequence_exact"] == 0.5
