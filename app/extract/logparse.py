"""Log evidence -> LogEvents -> Observations.

Seven line formats occur (one per log family), all of the shape "<ISO timestamp> <rest>":
  logfmt   : level=INFO service=x trace_id=t event=e k=v k="v"      (also "INFO service=..." variant)
  json     : {"severity":..,"service":..,"trace_id":..,"event":..,...}
  syslog   : <15>1 host SERVICE TRACE - - k="v" ...
  bracket  : [LEVEL] [service] [trace] event: k="v" ...
  pipe     : | LEVEL | service | trace | event | k="v" ...
  cloud    : requestId=TRACE level=X function=SERVICE message="k=\\"v\\" ..."
Each line is normalized to LogEvent(ts, service, trace_id, event, fields). Any event naming a resource_id
becomes an Observation; the lookup key from the same trace's *_started event is attached, because the
completed line often carries only the resource id while the started line carries the person's identifier.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.extract.lookup import lookup_observation
from app.extract.normalize import clean, parse_ts, split_record, system_for
from app.schemas import Observation

KV_RE = re.compile(r'([A-Za-z_][\w.]*)=("(?:[^"\\]|\\.)*"|\S+)')
SYSLOG_RE = re.compile(r"^<\d+>\d+ (\S+) (\S+) (\S+) - - (.*)$")
BRACKET_RE = re.compile(r"^\[(\w+)\] \[([^\]]+)\] \[([^\]]+)\] ([\w.]+): ?(.*)$")
LEVELS = {"DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL", "TRACE"}
KEY_FIELDS = ("lookup_key", "match_key", "scan_key", "subject_key", "owner_key", "correlation_key", "query")
# The started line's key, in priority order; subject_ref (security audit queries) is also an identifier.
TRACE_KEY_FIELDS = KEY_FIELDS + ("subject_ref",)
# Event-name prefix -> data_category. The pii-scan split is the only place logs distinguish a support
# message (REDACT) from an attachment (DELETE).
EVENT_CATEGORY = [
    ("message_pii_scan", "SUPPORT_MESSAGE"), ("message_read", "SUPPORT_MESSAGE"),
    ("support_ticket_", "SUPPORT_MESSAGE"),
    # A contact keyed by email hash with an email_opt_in flag is a marketing contact (rule_10), even when the
    # analytics platform stores it.
    ("contact_lookup", "MARKETING_PROFILE"),
    ("attachment_pii_scan", "SUPPORT_ATTACHMENT"), ("analytics_", "ANALYTICS_PROFILE"),
    ("profile_lookup", "USER_PROFILE"), ("index_query", "SEARCH_INDEX_ENTRY"), ("connector_", "DATA_FLOW"),
    ("restore_", "BACKUP_ARCHIVE"), ("export_", "USER_EXPORT"), ("audit_record_", "SECURITY_AUDIT_LOG"),
    ("retained_order", "LEGAL_HOLD_RECORD"),
]
# Events that mention a resource without reading its state.
NON_STATE_EVENTS = {"thumbnail_failed"}
# Services in sample answers. Any other service name (profile_service, support_replica...) is mapped to the
# system that owns the resource id prefix.
KNOWN_SYSTEMS = {"account_service", "search_service", "analytics_platform", "support_platform", "export_service",
                 "integration_service", "backup_service", "security_service", "identity_service"}


@dataclass
class LogEvent:
    line_no: int
    ts: datetime | None
    fmt: str
    service: str | None
    trace_id: str | None
    event: str | None
    fields: dict[str, str] = field(default_factory=dict)


def parse_kv(text: str) -> dict[str, str]:
    out = {}
    for key, raw in KV_RE.findall(text):
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1].replace('\\"', '"')
        out[key] = raw
    return out


def parse_line(line_no: int, line: str) -> LogEvent | None:
    line = line.rstrip("\n")
    if not line.strip():
        return None
    ts_text, _, rest = line.partition(" ")
    ts = parse_ts(ts_text)
    rest = rest.strip()
    if rest.startswith("{"):
        d = {k: str(v) for k, v in json.loads(rest).items()}
        return LogEvent(line_no, ts, "json", d.pop("service", None) or d.get("logger"), d.pop("trace_id", None),
                        d.pop("event", None), d)
    if m := SYSLOG_RE.match(rest):
        _host, service, trace, kv = m.groups()
        d = parse_kv(kv)
        return LogEvent(line_no, ts, "syslog", service, trace, d.pop("event", None), d)
    if m := BRACKET_RE.match(rest):
        _level, service, trace, event, kv = m.groups()
        return LogEvent(line_no, ts, "bracket", service, trace, event, parse_kv(kv))
    if rest.startswith("|"):
        parts = [p.strip() for p in rest.strip("|").split("|", 4)]
        if len(parts) == 5:
            _level, service, trace, event, kv = parts
            return LogEvent(line_no, ts, "pipe", service, trace, event, parse_kv(kv))
    d = parse_kv(rest)
    if "message" in d and "function" in d:  # cloud runtime: real fields live inside message="..."
        inner = parse_kv(d.pop("message"))
        return LogEvent(line_no, ts, "cloud", d.get("function"), d.get("requestId"), inner.pop("event", None),
                        {**inner, **{k: v for k, v in d.items() if k not in ("function", "requestId")}})
    first = rest.split(" ", 1)[0]
    if first in LEVELS:
        d.setdefault("level", first)
    return LogEvent(line_no, ts, "logfmt", d.pop("service", None), d.pop("trace_id", None), d.pop("event", None), d)


def _category(event: str | None) -> str | None:
    for prefix, category in EVENT_CATEGORY:
        if event and event.startswith(prefix):
            return category
    return None


def _present(event: str | None, f: dict[str, str]) -> bool | None:
    """Did the reader find the record? Explicit signals first (http_status, rows, hit/miss, available);
    otherwise a read/scan event that returned the record's content means the record exists."""
    if (v := clean(f.get("http_status"))) is not None:
        return v.startswith("2")
    if (v := clean(f.get("rows"))) is not None and v.isdigit():
        return int(v) > 0
    if (v := clean(f.get("result"))) is not None and v.lower() in ("hit", "miss"):
        return v.lower() == "hit"
    for key in ("archive_present", "available"):
        if (v := clean(f.get(key))) is not None:
            return v.lower() == "true"
    return None if event in NON_STATE_EVENTS else True


def events_to_observations(file_id: str, events: list[LogEvent]) -> list[Observation]:
    # First lookup key seen per trace (normally on the *_started line).
    trace_key: dict[str, str] = {}
    for e in events:
        if e.trace_id and e.trace_id not in trace_key:
            for k in TRACE_KEY_FIELDS:
                if clean(e.fields.get(k)):
                    trace_key[e.trace_id] = e.fields[k]
                    break
    out = []
    for e in events:
        f = e.fields
        rid = clean(f.get("resource_id"))
        if not rid or (e.event or "").endswith("_started"):
            continue  # *_started lines carry only the query; it is attached to the completion below
        loc = f"line {e.line_no}"
        if e.event == "record_lookup" and "result" in f and "system" in f:
            out.append(lookup_observation(file_id, loc, "log", e.ts, f["system"], rid, f.get("record_type"),
                                          f["result"], f.get("subject_reference")))
            continue
        system = e.service if e.service in KNOWN_SYSTEMS else (system_for(rid) or e.service or "unknown")
        own_key = next((f[k] for k in KEY_FIELDS if clean(f.get(k))), None)
        identifiers, attrs = split_record(f, skip={"resource_id", *KEY_FIELDS})
        attrs.update({"event": e.event, "service": e.service, "trace_id": e.trace_id, "log_format": e.fmt})
        out.append(Observation(
            file_id=file_id, locator=loc, source_kind="log", observed_at=e.ts, system_id=system, resource_id=rid,
            present=_present(e.event, f), data_category=_category(e.event), status_text=e.event, identifiers=identifiers, attrs=attrs,
            lookup_key=own_key or (trace_key.get(e.trace_id) if e.trace_id else None)))
    return out


def parse_log(file_id: str, path: Path) -> tuple[list[Observation], list[LogEvent]]:
    events = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        ev = parse_line(i, line)
        if ev is not None:
            events.append(ev)
    return events_to_observations(file_id, events), events
