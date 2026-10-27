"""deletion_rules.csv -> required treatment per data category."""
from __future__ import annotations

from app.schemas import DeletionRule

DIRECT_IDENTIFIERS = ("email", "phone", "name", "email_hash")
FLOW_CATEGORY = "DATA_FLOW"  # not a rules.csv category: a process, judged by flows.py


def treatment_by_category(rules: tuple[DeletionRule, ...]) -> dict[str, str]:
    return {r.data_category: r.required_action for r in rules}


def exposed_identifiers(treatment: str, identifiers: dict[str, str]) -> list[str]:
    """Identifiers the policy forbids on a record that is kept.

    REDACT (support message, email log, tombstone): direct identifiers must go; the stable internal user
    reference (user_ref) may stay, which is what makes a tombstone a tombstone (rule_15).
    ANONYMIZE (analytics, telemetry): the record must not be linkable at all, so user_ref counts too.
    """
    forbidden = DIRECT_IDENTIFIERS + (("user_ref",) if treatment == "ANONYMIZE" else ())
    return sorted(k for k in forbidden if identifiers.get(k))
