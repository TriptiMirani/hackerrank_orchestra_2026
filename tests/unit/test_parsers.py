"""Each deterministic parser on a tiny hand-made input in the dataset's real formats."""
import json
from datetime import datetime, timezone

from app.extract.jsonrec import parse_json
from app.extract.logparse import parse_line, parse_log
from app.extract.normalize import classify_reference, split_record
from app.extract.router import parse_evidence
from app.extract.sqlsnap import parse_sql
from app.extract.textrec import parse_text
from app.schemas import EvidenceFile, ParseStatus

T = datetime(2026, 9, 1, tzinfo=timezone.utc)


def test_sql_replica_drift_is_visible(tmp_path):
    p = tmp_path / "s.sql"
    p.write_text("""-- Captured at: 2026-09-02T23:00:00Z
BEGIN TRANSACTION;
CREATE TABLE customer_profiles_failover_replica (resource_id TEXT PRIMARY KEY, user_id TEXT, full_name TEXT,
  email TEXT, phone TEXT, account_status TEXT, updated_at TEXT);
INSERT INTO "customer_profiles_failover_replica" VALUES('account_005','user_05','Leila H','l@y.com','+212 6','ACTIVE','x');
CREATE TABLE customer_profiles_primary (resource_id TEXT PRIMARY KEY, user_id TEXT, full_name TEXT,
  email TEXT, phone TEXT, account_status TEXT, updated_at TEXT);
COMMIT;""", encoding="utf-8")
    obs, cov = parse_sql("evidence_54", p, None)
    (o,) = obs
    assert (o.system_id, o.resource_id, o.present, o.data_category) == ("account_service", "account_005", True,
                                                                         "USER_PROFILE")
    assert o.attrs["topology"] == "failover_replica"
    assert o.identifiers == {"user_ref": "user_05", "name": "Leila H", "email": "l@y.com", "phone": "+212 6"}
    assert o.observed_at == datetime(2026, 9, 2, 23, tzinfo=timezone.utc)
    assert {(c.collection, c.record_count) for c in cov} == {("customer_profiles_failover_replica", 1),
                                                              ("customer_profiles_primary", 0)}


def test_sql_payload_rows_and_inventory_scope(tmp_path):
    p = tmp_path / "s.sql"
    p.write_text("""CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
INSERT INTO snapshot_scope VALUES ('account_service', '2026-09-05T07:18:01Z');
CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, email TEXT, account_status TEXT, updated_at TEXT);
CREATE TABLE support_records (resource_id TEXT PRIMARY KEY, requester_email TEXT, updated_at TEXT);
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT, collection_name TEXT,
  record_json TEXT, updated_at TEXT);
INSERT INTO customer_data_primary VALUES('search_1','search_service','customer_search_documents',
  '{"resource_id":"search_1","document":{"email":"a@b.com","display_name":null}}','t');
""", encoding="utf-8")
    obs, cov = parse_sql("e", p, T)
    (o,) = obs
    assert o.identifiers == {"email": "a@b.com"} and o.data_category == "SEARCH_INDEX_ENTRY"
    covered = {(c.system_id, c.collection, c.record_count) for c in cov}
    # support_platform is not in snapshot_scope: its empty table must NOT count as proof of absence.
    assert ("account_service", "customer_profiles", 0) in covered
    assert not any(c.system_id == "support_platform" for c in cov)


def test_json_service_export_404_and_search_hit(tmp_path):
    p = tmp_path / "e.json"
    p.write_text(json.dumps({"exported_at": "2026-09-15T11:44:55Z", "region": "global", "source": "x",
        "service_exports": {
            "account_service": {"profile_lookups": [
                {"observed_at": "2026-09-15T09:31:40Z", "profile": None, "resource_id": "account_1", "status_code": 404}]},
            "search_service": {"index_queries": [
                {"observed_at": "2026-09-15T09:14:18Z", "query": "n@o.com", "resource_id": "search_1",
                 "hits": {"total": 1, "documents": [{"resource_id": "search_1", "email": "n@o.com"}]}}]}}}),
        encoding="utf-8")
    obs, _ = parse_json("evidence_92", p, None)
    by = {o.resource_id: o for o in obs}
    assert by["account_1"].present is False and by["account_1"].status_text == "404"
    assert by["search_1"].present is True and by["search_1"].identifiers == {"email": "n@o.com"}
    assert by["search_1"].lookup_key == "n@o.com"


def test_json_snapshot_bundle_empty_collection_is_coverage(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"exported_at": "x", "source": "y", "snapshots": [
        {"captured_at": "2026-09-15T12:00:00Z", "data_store": "account_service", "collection": "customer_profiles",
         "records": []},
        {"captured_at": "2026-09-15T12:00:00Z", "data_store": "identity_service",
         "collection": "deleted_customer_references",
         "records": [{"resource_id": "tombstone_1", "subject_reference": "user_5", "name": None, "email": None,
                      "phone": None, "email_hash": None, "account_status": "DELETED"}]}]}), encoding="utf-8")
    obs, cov = parse_json("evidence_51", p, None)
    (o,) = obs
    assert o.data_category == "ACCOUNT_TOMBSTONE" and o.identifiers == {"user_ref": "user_5"}
    assert {"name", "email", "phone", "email_hash"} <= set(o.attrs)  # present-but-null = redacted fields
    assert any(c.system_id == "account_service" and c.record_count == 0 for c in cov)


def test_json_lookup_ledger(tmp_path):
    p = tmp_path / "l.json"
    p.write_text(json.dumps({"exported_at": "x", "origin": "o", "record_count": 1, "records": [
        {"recorded_at": "2026-08-12T09:00:00Z", "system": "account_service", "resource_id": "account_1",
         "record_type": "USER_PROFILE", "result": "NOT_FOUND", "subject_reference": "M@X.COM"}]}), encoding="utf-8")
    (o,), _ = parse_json("e", p, None)
    assert o.present is False and o.identifiers == {"email": "M@X.COM"} and o.data_category == "USER_PROFILE"


LINES = {
    "logfmt": '2026-09-26T07:57:13Z level=INFO service=search_service trace_id=rb-1 event=index_query_completed '
              'resource_id=search_4262 result=hit total_hits=1 email=a@b.com customer_reference=null',
    "json": '2026-09-10T08:56:20Z {"severity":"DEBUG","service":"account_service","trace_id":"tr-5",'
            '"resource_id":"account_6633","event":"profile_lookup_completed","rows":"1","http_status":"200"}',
    "syslog": '2026-08-22T10:51:17Z <15>1 node-1 account_service tr-4973247 - - level=DEBUG '
              'resource_id="account_1785" event="profile_lookup_completed" rows="1" http_status="200"',
    "bracket": '2026-07-28T05:51:21Z [INFO] [account_service] [tr-6074163] profile_lookup_completed: '
               'resource_id="account_7947" rows="0" http_status="404"',
    "pipe": '2026-07-03T07:51:32Z | DEBUG | security_service | tr-5156005 | audit_record_read_completed | '
            'resource_id="audit_record_4413" rows="1" correlation_key="user_211"',
    "cloud": '2026-09-30T11:51:03Z requestId=tr-2263120 level=DEBUG function=profile_service message="instance='
             '\\"profile-3\\" resource_id=\\"account_66777\\" event=\\"profile_lookup_completed\\" rows=\\"1\\" '
             'correlation_key=\\"a.w@yahoo.com\\""',
}


def test_every_log_format_normalizes():
    for fmt, line in LINES.items():
        ev = parse_line(1, line)
        assert ev.fmt == fmt, fmt
        assert ev.event and ev.event.endswith("_completed"), fmt
        assert ev.fields.get("resource_id"), fmt


def test_log_trace_assembly_and_aliases(tmp_path):
    p = tmp_path / "t.log"
    p.write_text("\n".join([
        '2026-09-15T11:58:08Z level=DEBUG service=integration_service trace_id=tr-1 event=connector_config_read_started '
        'subject_key="lucia@g.com"',
        '2026-09-15T11:58:11Z level=WARN service=integration_service trace_id=tr-1 resource_id=data_flow_9846 '
        'event=connector_heartbeat enabled=true',
        '2026-09-30T11:51:03Z requestId=tr-2 level=DEBUG function=profile_service message="resource_id=\\"account_7\\" '
        'event=\\"profile_lookup_completed\\" rows=\\"0\\" http_status=\\"404\\""',
        '2026-09-15T11:58:26Z level=DEBUG service=analytics_platform trace_id=tr-3 resource_id=analytics_9914 '
        'event=contact_lookup_completed rows=1 correlation_key="' + "a" * 64 + '"',
    ]), encoding="utf-8")
    obs, _ = parse_log("evidence_01", p)
    by = {o.resource_id: o for o in obs}
    assert len(obs) == 3  # the *_started line is not an observation
    assert by["data_flow_9846"].lookup_key == "lucia@g.com" and by["data_flow_9846"].data_category == "DATA_FLOW"
    assert by["account_7"].system_id == "account_service" and by["account_7"].present is False  # alias mapped
    # correlation_key is the reader's query key, not stored PII: lookup_key, never identifiers
    assert by["analytics_9914"].identifiers == {} and by["analytics_9914"].lookup_key == "a" * 64


def test_text_handover_rows(tmp_path):
    p = tmp_path / "h.txt"
    p.write_text("HANDOVER\n- 2026-10-01T12:00:00Z | analytics_platform | analytics_6830 | ANALYTICS_PROFILE | "
                 "ANONYMIZED | no direct identifier\n", encoding="utf-8")
    obs, lines = parse_text("evidence_03", p)
    (o,) = obs
    assert o.present is True and o.status_text == "ANONYMIZED" and o.identifiers == {}
    assert o.attrs == {"subject_reference": "no direct identifier"} and len(lines) == 2


def test_reference_classification_and_split():
    assert classify_reference("user_15") == "user_ref"
    assert classify_reference("a" * 64) == "email_hash"
    assert classify_reference("+81 90 6156 0005") == "phone"
    assert classify_reference("no direct identifier") is None
    ids, attrs = split_record({"requester_email": "x@y.com", "requester_name": "null", "enabled": "true"})
    assert ids == {"email": "x@y.com"} and attrs == {"requester_name": None, "enabled": "true"}


def test_router_turns_bad_file_into_state(tmp_path):
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "bad.json").write_text("{not json", encoding="utf-8")
    ef = EvidenceFile(file_id="e1", file_path="evidence/bad.json", origin="o", captured_at=T)
    res = parse_evidence(ef, tmp_path)
    assert res.status is ParseStatus.PARSE_FAILED and "JSONDecodeError" in res.error
    ef_png = EvidenceFile(file_id="e2", file_path="evidence/x.png", origin="o", captured_at=T)
    assert parse_evidence(ef_png, tmp_path).status is ParseStatus.NEEDS_OCR


def test_replica_payload_snapshot_covers_the_profile_store_even_when_empty(tmp_path):
    # A subject's customer_data snapshot without any profile row proves the profile is gone (subject-scoped).
    p = tmp_path / "r.sql"
    p.write_text("""-- Captured at: 2026-08-27T12:00:00Z
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT, collection_name TEXT,
  record_json TEXT, updated_at TEXT);
INSERT INTO customer_data_primary VALUES('audit_record_1','security_service','security_audit_events',
  '{"resource_id":"audit_record_1","subject_reference":"user_101"}','t');
""", encoding="utf-8")
    _, cov = parse_sql("evidence_10", p, None)
    acct = [c for c in cov if c.system_id == "account_service"]
    assert len(acct) == 1 and acct[0].record_count == 0 and acct[0].scoped
