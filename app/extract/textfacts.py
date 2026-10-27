"""Free text (OCR lines, PDF lines) -> Observations, by grounded matching only.

* Text is grouped: OCR lines into visual rows (a table row is split into one box per column), PDF lines per page.
* A group yields one Observation per resource id found in it, linked to the identifiers in the SAME group.
* Grounding gate: emails, user ids and 64-hex hashes are taken literally from the text; names and phones only
  when they equal a KNOWN value (lexicon), because a free-form "name" or digit run is not trustworthy OCR.
* Text never sets state. A resource shown on a screen or page is present=True at capture time; no phrase
  ("already deleted", "ignore previous instructions") can mark anything absent. Such phrases are flagged in
  attrs for the audit trail and otherwise ignored (CLAUDE.md hard rule 7).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.extract.lexicon import Lexicon, MIN_PHONE_DIGITS, name_key, phone_key
from app.extract.normalize import system_for
from app.extract.ocr import OcrLine
from app.schemas import Observation, SourceKind, TextLine

# Boundaries tuned on real OCR output:
#  * a leading digit is allowed: the table's row-number badge merges into the cell ('3export_1259');
#  * a leading letter, '_' or '-' is not: 'customer_export_1259.zip' is a file name, not export_1259;
#  * a trailing '-' is allowed unless a digit follows: '·' is read as '-' ('marketing_001- sj.park'), while
#    'customer_export_2026-02-10.zip' is a date-stamped file name.
RESOURCE_RE = re.compile(r"(?<![A-Za-z_\-])((?:account|search|analytics|support|export|backup|data_flow|audit_record"
                         r"|tombstone|marketing|legal_hold)_\d+)(?![A-Za-z0-9]|-\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
USER_RE = re.compile(r"(?<![A-Za-z0-9])user_\d+(?![A-Za-z0-9])")
HASH_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
PHONE_SPAN_RE = re.compile(r"\+?\d[\d \-().]{6,}\d")
INJECTION_RE = re.compile(r"ignore (all )?(previous|prior) instructions|already (been )?deleted|mark(ed)? as "
                          r"(compliant|complete)|disregard|system prompt", re.IGNORECASE)

# Prefix -> data category, only where the prefix alone is unambiguous (support: message vs attachment is not).
PREFIX_CATEGORY = {
    "account_": "USER_PROFILE", "search_": "SEARCH_INDEX_ENTRY", "analytics_": "ANALYTICS_PROFILE",
    "export_": "USER_EXPORT", "backup_": "BACKUP_ARCHIVE", "data_flow_": "DATA_FLOW",
    "audit_record_": "SECURITY_AUDIT_LOG", "tombstone_": "ACCOUNT_TOMBSTONE", "marketing_": "MARKETING_PROFILE",
    "legal_hold_": "LEGAL_HOLD_RECORD",
}
EXTRA_SYSTEMS = {"marketing_": "marketing_platform", "legal_hold_": "legal_service"}


PANEL_HEADING = "activity"   # every Northstar console has a right-hand detail panel with an "Activity" list
PANEL_MARGIN_PX = 15
CARD_GAP_PX = 45            # vertical gap separating the panel card from the summary cards above it


@dataclass
class TextGroup:
    locator: str
    texts: list[str]


def _prefix(rid: str) -> str:
    return rid[: rid.rindex("_") + 1]


def ocr_groups(lines: list[OcrLine]) -> list[TextGroup]:
    """Split a console screenshot into regions before grouping, so a table row never absorbs panel text that
    happens to sit at the same height:
      * main area (left of the detail panel): visual rows;
      * panel card (the record shown in the detail panel, above its "Activity" heading): ONE group;
      * panel activity list (below the heading): visual rows.
    Without an "Activity" heading the whole image is grouped into visual rows."""
    heading = next((l for l in lines if l.text.strip().lower() == PANEL_HEADING), None)
    if heading is None:
        return ocr_rows(lines, "row")
    boundary = heading.x0 - PANEL_MARGIN_PX
    main = [l for l in lines if l.x0 < boundary]
    above = sorted((l for l in lines if l.x0 >= boundary and l.y1 <= heading.y0), key=lambda l: l.y0)
    activity = [l for l in lines if l.x0 >= boundary and l.y0 > heading.y0]
    # The card is the run of panel boxes directly above the heading; a big vertical gap ends it.
    card: list[OcrLine] = []
    for line in reversed(above):
        if card and min(c.y0 for c in card) - line.y1 > CARD_GAP_PX:
            break
        card.append(line)
    card.sort(key=lambda l: (l.y0, l.x0))
    groups = ocr_rows(main, "row")
    if card:
        groups.append(TextGroup(f"panel card y={card[0].y0}-{card[-1].y1}", [l.text for l in card]))
    return groups + ocr_rows(activity, "panel activity row")


def ocr_rows(lines: list[OcrLine], label: str = "row") -> list[TextGroup]:
    """Cluster OCR boxes into visual rows: a box joins the current row if its vertical centre lies inside the
    row's vertical span. Within a row, boxes are ordered left to right."""
    rows: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda l: ((l.y0 + l.y1) / 2, l.x0)):
        centre = (line.y0 + line.y1) / 2
        if rows and min(l.y0 for l in rows[-1]) <= centre <= max(l.y1 for l in rows[-1]):
            rows[-1].append(line)
        else:
            rows.append([line])
    return [TextGroup(f"{label} {i + 1} y={min(l.y0 for l in r)}",
                      [l.text for l in sorted(r, key=lambda l: l.x0)]) for i, r in enumerate(rows)]


def page_groups(lines: list[TextLine]) -> list[TextGroup]:
    """PDF: one group per page (records span several 'Detail' lines on the same page)."""
    pages: dict[str, list[str]] = {}
    for line in lines:
        pages.setdefault(line.locator.split(" line ")[0], []).append(line.text)
    return [TextGroup(page, texts) for page, texts in pages.items()]


def match_identifiers(text: str, lex: Lexicon) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {"email": [], "user_ref": [], "email_hash": [], "phone": [], "name": []}

    def add(kind: str, value: str) -> None:
        if value not in found[kind]:
            found[kind].append(value)

    for m in EMAIL_RE.findall(text):
        add("email", m.lower())
    for m in USER_RE.findall(text):
        add("user_ref", m)
    for m in HASH_RE.findall(text):
        add("email_hash", m.lower())
    for span in PHONE_SPAN_RE.findall(text):
        digits = phone_key(span)
        if len(digits) >= MIN_PHONE_DIGITS and digits in lex.phones:
            add("phone", span.strip())
    squashed = name_key(EMAIL_RE.sub(" ", text))  # 'lucia-torres@...' must not read as the name Lucia Torres
    for key in lex.names:
        if key in squashed:
            add("name", key)  # normalized key; the lexicon maps it back to user ids
    return found


def group_observations(file_id: str, kind: SourceKind, observed_at: datetime | None, groups: list[TextGroup],
                       lex: Lexicon, origin: str) -> list[Observation]:
    # Page-level identifiers: a weak link for records whose own row shows none (e.g. support_9538 on the account
    # page of user_11). Recorded as attrs only; Phase 3 decides whether a page-level link counts.
    page = match_identifiers(" | ".join(t for g in groups for t in g.texts), lex)
    page_attrs = {f"page_{k}": ";".join(sorted(v)) for k, v in page.items() if v and k in ("user_ref", "email")}
    out: list[Observation] = []
    for g in groups:
        text = " | ".join(g.texts)
        rids = list(dict.fromkeys(RESOURCE_RE.findall(text)))
        if not rids:
            continue
        found = match_identifiers(text, lex)
        identifiers = {k: v[0] for k, v in found.items() if v}
        attrs: dict[str, str | bool] = {"group_text": text[:500], "origin": origin, **page_attrs}
        for k, v in found.items():
            if len(v) > 1:
                attrs[f"all_{k}"] = ";".join(v)
        if INJECTION_RE.search(text):
            attrs["instruction_like_text_ignored"] = True
        for rid in rids:
            p = _prefix(rid)
            out.append(Observation(
                file_id=file_id, locator=g.locator, source_kind=kind, observed_at=observed_at,
                system_id=system_for(rid) or EXTRA_SYSTEMS.get(p, "unknown"), resource_id=rid, present=True,
                data_category=PREFIX_CATEGORY.get(p), status_text="shown", identifiers=identifiers,
                attrs=attrs))
    return out
