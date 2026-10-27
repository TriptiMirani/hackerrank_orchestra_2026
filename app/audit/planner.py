"""CaseAssessment -> ordered steps_json. Deterministic; the LLM never chooses operations or order (hard rule 6).

Ordering contract (docs/conventions.md C1): phase FINDING -> REMEDIATION -> VERIFICATION; within a phase the
brief's operation-table order; then system_id, then resource_id; step_no 1..n.
Citations (C2): COMPLETE = verification only, each citing the evidence of the current state; INCOMPLETE = findings
cite evidence, remediation and post-remediation verification are future work with evidence_id "".
"""
from __future__ import annotations

import json

from app.audit.status import CaseAssessment, Item, Verdict

PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]
OP_ORDER = {
    "FINDING": ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"],
    "REMEDIATION": ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"],
    "VERIFICATION": ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
                     "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"],
}
STATE_OF = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT", "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE", "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
# Verdict of a compliant record -> the check proving it.
VERIFY_OF = {
    Verdict.ABSENT_OK: "VERIFY_RECORD_ABSENT", Verdict.REDACTED_OK: "VERIFY_RECORD_REDACTED",
    Verdict.ANONYMIZED_OK: "VERIFY_RECORD_ANONYMIZED", Verdict.RETAINED_OK: "VERIFY_RECORD_PRESENT",
    Verdict.FLOW_DISABLED_OK: "VERIFY_DATA_FLOW_DISABLED", Verdict.RESTORE_BLOCKED_OK: "VERIFY_RESTORE_BLOCKED",
    Verdict.UNKNOWN: "VERIFY_RECORD_ABSENT",   # only the primary account survives as UNKNOWN: it must be gone
}
KEY_ORDER = ["step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def _violation_ops(item: Item) -> tuple[str, str, str]:
    """(finding, remediation, verification) for a violation."""
    v = item.verdict
    if v is Verdict.REMAINING:
        return "REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"
    if v is Verdict.EXPOSED:
        if item.treatment == "ANONYMIZE":
            return "REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"
        return "REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"
    if v is Verdict.ACTIVE_FLOW:
        return "REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"
    if v is Verdict.RESTORE_RISK:
        return "REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"
    raise ValueError(f"not a violation: {v}")


def _step(phase: str, item: Item, op: str, evidence_id: str) -> dict:
    return {"phase": phase, "system_id": item.resource.system_id, "resource_id": item.resource.resource_id,
            "operation": op, "state": STATE_OF[op], "evidence_id": evidence_id}


def canonical_sort(steps: list[dict]) -> list[dict]:
    ordered = sorted(steps, key=lambda s: (PHASES.index(s["phase"]), OP_ORDER[s["phase"]].index(s["operation"]),
                                           s["system_id"], s["resource_id"]))
    return [{"step_no": i, **{k: s[k] for k in KEY_ORDER[1:]}} for i, s in enumerate(ordered, start=1)]


def plan_steps(a: CaseAssessment) -> list[dict]:
    if a.status == "NOT_APPLICABLE":
        return []
    if a.status == "ERROR":
        return []   # never written: run_all aborts on ERROR cases; the validator rejects the status
    steps: list[dict] = []
    if a.status == "COMPLETE":
        # A COMPLETE verification must cite the evidence of the current state. A record with no such evidence
        # (e.g. an account whose state is UNKNOWN) gets no step rather than an uncited, unproven claim.
        for it in a.items:
            if it.resource.evidence_id:
                steps.append(_step("VERIFICATION", it, VERIFY_OF[it.verdict], it.resource.evidence_id))
        return canonical_sort(steps)
    for it in a.items:
        if it.verdict in (Verdict.REMAINING, Verdict.EXPOSED, Verdict.ACTIVE_FLOW, Verdict.RESTORE_RISK):
            finding, remediation, verification = _violation_ops(it)
            steps.append(_step("FINDING", it, finding, it.resource.evidence_id))
            steps.append(_step("REMEDIATION", it, remediation, ""))
            steps.append(_step("VERIFICATION", it, verification, ""))
        else:
            steps.append(_step("VERIFICATION", it, VERIFY_OF[it.verdict], ""))
    return canonical_sort(steps)


def to_steps_json(steps: list[dict]) -> str:
    # Fixed key order + compact separators: identical input always gives byte-identical output.
    return json.dumps(steps, ensure_ascii=False, separators=(",", ":"))
