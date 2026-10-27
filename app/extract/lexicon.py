"""Every identifier value known from the identity snapshot and the audit-log history.

Used to recognise names and phones in free text (OCR, PDF), where a pattern alone cannot be trusted.
Values map to user_ids so later phases can tell whose identifier a match is. No per-case logic here.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from app.schemas import AuditLogEntry, UserIdentity

MIN_NAME_KEY = 6     # shorter normalized names are too likely to occur inside other words
MIN_PHONE_DIGITS = 8


def email_key(value: str) -> str:
    return value.strip().casefold()


def phone_key(value: str) -> str:
    return re.sub(r"\D", "", value)


def name_key(value: str) -> str:
    """Casefold, strip accents, keep letters only: OCR drops spaces ('GabrielSilva') and accents vary."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in decomposed if ch.isalpha() and not unicodedata.combining(ch))


@dataclass
class Lexicon:
    emails: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    phones: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    names: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    hashes: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    user_ids: set[str] = field(default_factory=set)

    def add(self, kind: str, value: str, user_id: str) -> None:
        if not value:
            return
        if kind == "email":
            self.emails[email_key(value)].add(user_id)
        elif kind == "phone" and len(phone_key(value)) >= MIN_PHONE_DIGITS:
            self.phones[phone_key(value)].add(user_id)
        elif kind == "name" and len(name_key(value)) >= MIN_NAME_KEY:
            self.names[name_key(value)].add(user_id)
        elif kind == "email_hash":
            self.hashes[value.strip().lower()].add(user_id)


def build_lexicon(identities: tuple[UserIdentity, ...], audit_log: tuple[AuditLogEntry, ...]) -> Lexicon:
    lex = Lexicon()
    for u in identities:
        lex.user_ids.add(u.user_id)
        for kind, value in (("email", u.email), ("phone", u.phone), ("name", u.name), ("email_hash", u.email_hash)):
            lex.add(kind, value, u.user_id)
    for a in audit_log:  # historical values: old emails/phones still appear in older systems
        lex.add(a.field_name, a.old_value, a.user_id)
        lex.add(a.field_name, a.new_value, a.user_id)
    return lex
