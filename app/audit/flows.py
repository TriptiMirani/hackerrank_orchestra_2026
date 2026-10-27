"""Active data flows and restore paths: can a process copy or recreate the subject's data?

Judged from the latest eligible evidence of the flow/backup itself (enabled flags, restore policy, lookup
results). Workflow results such as "DISABLE_DATA_FLOW SUCCESS" are claims and are not consulted.
"""
from __future__ import annotations

import re

from app.extract.normalize import to_bool

# Screenshot rows/cards (status_text "shown") state settings in words. Only the record's OWN row/card text is read
# (attrs["group_text"], built in textfacts.py), negative words are checked first, and anything else is "not stated".
FLOW_OFF_WORDS = ("disabled", "paused", "stopped", "inactive", "suspended")
FLOW_ON_WORDS = ("continuous", "healthy", "enabled", "running")
RESTORE_BLOCKED_WORDS = ("blocked", "restore disabled", "locked", "legal hold", "deletion block")
RESTORE_OPEN_WORDS = ("archive", "tar.zst", "tar.gz")   # a stored, restorable archive with no block shown


def _screen_text(status_text: str | None, attrs: dict) -> str | None:
    return str(attrs.get("group_text", "")).lower() if status_text == "shown" else None


def _says(text: str, words: tuple[str, ...]) -> bool:
    """Whole-word match that is not negated: 'unblocked', 'unlocked', 'not stopped', 'no legal hold' do not count."""
    for w in words:
        for m in re.finditer(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text):
            before = text[:m.start()]
            # negated if directly preceded by a negation word ("no legal hold", "not stopped", "non-blocked")
            if not re.search(r"(?<![a-z])(?:no|not|never|non)[\s-]*$", before):
                return True
    return False


def flow_active(status_text: str | None, attrs: dict) -> bool | None:
    """True = can still copy data, False = disabled, None = evidence does not say."""
    if status_text in ("ACTIVE",):
        return True
    if status_text in ("DISABLED",):
        return False
    enabled = to_bool(attrs.get("enabled"))
    if enabled is not None:
        return enabled
    if (text := _screen_text(status_text, attrs)) is not None:
        if _says(text, FLOW_OFF_WORDS):
            return False
        if _says(text, FLOW_ON_WORDS):
            return True
    return None


def restore_open(status_text: str | None, attrs: dict) -> bool | None:
    """True = the backup can restore the subject's data, False = blocked, None = unknown."""
    if status_text == "ACTIVE":
        return True
    if status_text == "BLOCKED":
        return False
    allowed = to_bool(attrs.get("restore_allowed"))
    if allowed is not None:
        return allowed
    if (decision := attrs.get("decision")) in ("deny", "allow"):
        return decision == "allow"
    eligible, block = to_bool(attrs.get("eligible")), to_bool(attrs.get("deletion_block"))
    if block is True or eligible is False:
        return False
    if block is False or eligible is True:
        # deletion_block=false alone says nothing blocks the restore (a missing 'eligible' is not a block)
        return True
    if (text := _screen_text(status_text, attrs)) is not None:
        if _says(text, RESTORE_BLOCKED_WORDS):
            return False
        if _says(text, RESTORE_OPEN_WORDS):
            return True
    return None
