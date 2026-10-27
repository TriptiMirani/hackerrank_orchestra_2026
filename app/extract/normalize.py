"""Shared normalization for all deterministic parsers.

Sources name the same thing differently (requester_email / owner_email / customer_email). This module
maps them onto one vocabulary so later phases compare like with like:
  identifiers: email, phone, name, email_hash, user_ref
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# Field name (lower-case) -> normalized identifier key.
IDENTIFIER_FIELDS = {
    "email": "email", "requester_email": "email", "owner_email": "email", "customer_email": "email",
    "phone": "phone", "requester_phone": "phone", "owner_phone": "phone",
    "full_name": "name", "display_name": "name", "requester_name": "name", "name": "name",
    "email_hash": "email_hash",
    "customer_reference": "user_ref", "subject_reference": "user_ref", "user_id": "user_ref",
    "subject_ref": "user_ref", "matched_value": "user_ref",
}
# Not here on purpose: correlation_key / lookup_key / match_key are what a reader QUERIED with, not data the
# record holds (a 404 lookup still has one). Parsers keep them as Observation.lookup_key.

# Resource-id prefix -> owning system. Used only when the source does not say the system itself.
PREFIX_SYSTEM = [
    ("data_flow_", "integration_service"), ("audit_record_", "security_service"),
    ("account_", "account_service"), ("search_", "search_service"), ("analytics_", "analytics_platform"),
    ("support_", "support_platform"), ("export_", "export_service"), ("backup_", "backup_service"),
    ("tombstone_", "identity_service"),
]

# Source collection/table name -> deletion_rules data_category, only where the name makes it unambiguous.
COLLECTION_CATEGORY = {
    "customer_profiles": "USER_PROFILE",
    "customer_search_documents": "SEARCH_INDEX_ENTRY", "search_documents": "SEARCH_INDEX_ENTRY",
    "customer_analytics_profiles": "ANALYTICS_PROFILE", "customer_analytics_reports": "ANALYTICS_PROFILE",
    "support_conversations": "SUPPORT_MESSAGE", "ticket_attachments": "SUPPORT_ATTACHMENT",
    "support_records": "SUPPORT_MESSAGE",  # inventory rows carry a message_excerpt: a conversation
    "customer_exports": "USER_EXPORT", "customer_export_jobs": "USER_EXPORT",
    "backup_restore_entries": "BACKUP_ARCHIVE", "backup_catalog": "BACKUP_ARCHIVE",
    "security_audit_records": "SECURITY_AUDIT_LOG", "security_audit_events": "SECURITY_AUDIT_LOG",
    "deleted_customer_references": "ACCOUNT_TOMBSTONE", "retained_order_records": "LEGAL_HOLD_RECORD",
    "connector_subscriptions": "DATA_FLOW", "connector_configurations": "DATA_FLOW",
}

NULLS = {"", "null", "none", "nil", "-", "—"}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USER_RE = re.compile(r"^user_\d+$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_PHONE_RE = re.compile(r"^\+?[\d ()\-]{7,}$")


def clean(value: Any) -> Any:
    """'null'/'None'/'' -> None; strip strings; leave other types alone."""
    if isinstance(value, str):
        v = value.strip()
        return None if v.lower() in NULLS else v
    return value


def to_bool(value: Any) -> bool | None:
    v = clean(value)
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).lower()
    if s in ("true", "1", "yes", "y"):
        return True
    if s in ("false", "0", "no", "n"):
        return False
    return None


def parse_ts(value: Any) -> datetime | None:
    v = clean(value)
    if v is None:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def system_for(resource_id: str) -> str | None:
    for prefix, system in PREFIX_SYSTEM:
        if resource_id.startswith(prefix):
            return system
    return None


def category_for(collection: str | None) -> str | None:
    if not collection:
        return None
    name = collection.lower()
    for suffix in ("_primary", "_failover_replica", "_read_replica", "_reporting_replica"):
        name = name.removesuffix(suffix)
    return COLLECTION_CATEGORY.get(name)


def classify_reference(value: str) -> str | None:
    """A free reference (subject_reference, correlation_key, matched_value...) can be an email, a user_id,
    an email hash (64 hex) or a phone; say which. Anything else is not an identifier."""
    if _EMAIL_RE.match(value):
        return "email"
    if _USER_RE.match(value):
        return "user_ref"
    if _HASH_RE.match(value.lower()):
        return "email_hash"
    if _PHONE_RE.match(value):
        return "phone"
    return None


def split_record(record: dict[str, Any], skip: set[str] = frozenset()) -> tuple[dict[str, str], dict[str, Any]]:
    """Split a flat record into normalized identifiers and remaining scalar attributes.

    Null identifier fields are kept out of `identifiers` but recorded in attrs as None, so later phases
    can tell "field exists and is empty" (redacted) from "field not in this source".
    """
    identifiers: dict[str, str] = {}
    attrs: dict[str, Any] = {}
    for key, raw in record.items():
        if key in skip:
            continue
        value = clean(raw)
        ident = IDENTIFIER_FIELDS.get(key.lower())
        if ident:
            if value is None:
                attrs[f"{key}"] = None
            elif ident == "user_ref" and key.lower() in ("subject_reference", "subject_ref", "matched_value"):
                kind = classify_reference(str(value))
                if kind:
                    identifiers.setdefault(kind, str(value))
                else:
                    attrs[key] = value
            else:
                identifiers.setdefault(ident, str(value))
        elif isinstance(value, (str, int, float, bool)) or value is None:
            attrs[key] = value
        # nested dicts/lists (metrics, recent_activity) are not identifiers; skipped on purpose
    return identifiers, attrs
