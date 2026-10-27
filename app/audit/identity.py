"""Identity resolution: request email -> the subject's user_id and every identifier valid at or before audit_at.

Rules (all confirmed on the data, see PROJECT_STATE.md):
  * email_hash = sha256(lowercase(email)), 850/850 current and 107/107 historical hashes match;
  * the request email may be current OR historical (100/518 requests resolve only through the audit log);
  * values introduced by identity changes AFTER audit_at are excluded (they did not exist at audit time),
    and the snapshot is rolled back through such changes.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel, PrivateAttr

from app.extract.lexicon import email_key, name_key, phone_key
from app.schemas import AuditLogEntry, UserIdentity


class Identity(BaseModel):
    user_id: str
    request_email: str
    matched_via: str                  # "current_email" | "historical_email"
    emails: list[str]                 # normalized (casefold), current first
    phones: list[str]                 # as written
    names: list[str]
    email_hashes: list[str]
    current_email: str
    current_name: str
    # Normalized forms, computed once: matches() runs for every observation of every case.
    _email_keys: set[str] = PrivateAttr(default_factory=set)
    _phone_keys: set[str] = PrivateAttr(default_factory=set)
    _name_keys: set[str] = PrivateAttr(default_factory=set)

    def model_post_init(self, __context) -> None:
        self._email_keys = {email_key(e) for e in self.emails}
        self._phone_keys = {phone_key(p) for p in self.phones}
        self._name_keys = {name_key(n) for n in self.names}

    def email_keys(self) -> set[str]:
        return self._email_keys

    def phone_keys(self) -> set[str]:
        return self._phone_keys

    def name_keys(self) -> set[str]:
        return self._name_keys

    def matches(self, kind: str, value: str) -> bool:
        """Does an identifier value found in evidence belong to this subject?"""
        if not value:
            return False
        if kind == "email":
            return email_key(value) in self.email_keys()
        if kind == "phone":
            return phone_key(value) in self.phone_keys()
        if kind == "name":
            return name_key(value) in self.name_keys()
        if kind == "email_hash":
            return value.strip().lower() in self.email_hashes
        if kind == "user_ref":
            return value.strip() == self.user_id
        return False

    def matches_any(self, value: str) -> bool:
        """For untyped keys (lookup keys): try every identifier kind."""
        return any(self.matches(k, value) for k in ("email", "user_ref", "email_hash", "phone", "name"))


def email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


class UnresolvedIdentity(LookupError):
    pass


def resolve_identity(request_email: str, audit_at: datetime, identities: tuple[UserIdentity, ...],
                     audit_log: tuple[AuditLogEntry, ...]) -> Identity:
    key = email_key(request_email)
    current = {email_key(u.email): u for u in identities}
    via = "current_email"
    user = current.get(key)
    if user is None:
        owners = {a.user_id for a in audit_log if a.field_name == "email" and key in (email_key(a.old_value),
                                                                                     email_key(a.new_value))}
        if len(owners) != 1:
            raise UnresolvedIdentity(f"{request_email}: {len(owners)} candidate users")
        user = next(u for u in identities if u.user_id in owners)
        via = "historical_email"

    changes = sorted((a for a in audit_log if a.user_id == user.user_id), key=lambda a: a.event_time)
    values = {"email": [user.email], "phone": [user.phone], "name": [user.name], "email_hash": [user.email_hash]}
    # Roll the snapshot back through changes made after audit_at: their new values never existed at audit time.
    for a in reversed([c for c in changes if c.event_time > audit_at]):
        values[a.field_name] = [v for v in values[a.field_name] if v != a.new_value] or [a.old_value]
    for a in changes:
        if a.event_time <= audit_at:
            values[a.field_name].append(a.old_value)
            values[a.field_name].append(a.new_value)
    # If the current email did not exist yet at audit_at, its hash did not either (even when the audit log has no
    # separate email_hash row for that change).
    if user.email not in values["email"]:
        values["email_hash"] = [v for v in values["email_hash"] if v != user.email_hash]
    # Every email held at or before audit_at implies its hash, even if the audit log omitted the hash change.
    hashes = values["email_hash"] + [email_hash(e) for e in values["email"]]
    dedupe = lambda xs: list(dict.fromkeys(xs))  # noqa: E731  (keeps first-seen order: current first)
    return Identity(
        user_id=user.user_id, request_email=request_email, matched_via=via,
        emails=dedupe(email_key(e) for e in values["email"]), phones=dedupe(values["phone"]),
        names=dedupe(values["name"]), email_hashes=dedupe(h.lower() for h in hashes),
        current_email=values["email"][0], current_name=values["name"][0])
