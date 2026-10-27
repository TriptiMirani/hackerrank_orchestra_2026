import pytest
from pydantic import ValidationError

from app.ingest.loaders import load_csv
from app.schemas import DeletionRequest, SystemActivity


def test_loads_typed_rows(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("case_id,email,requested_at,reported_status,reported_at,audit_at,request_text\n"
                 'case_01,a@b.com,2026-08-18T09:14:00Z,COMPLETE,2026-08-20T16:42:00Z,2026-09-03T11:30:00Z,"Hi, delete"\n',
                 encoding="utf-8")
    (row,) = load_csv(p, DeletionRequest)
    assert row.case_id == "case_01" and row.audit_at.year == 2026 and row.request_text == "Hi, delete"


def test_rejects_wrong_columns(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("case_id,email\ncase_01,a@b.com\n", encoding="utf-8")
    with pytest.raises(ValueError, match="columns"):
        load_csv(p, DeletionRequest)


def test_rejects_unknown_enum_value(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("activity_id,event_time,event_type,source_system,target_system,resource_id,reported_result,details\n"
                 "activity_01,2026-01-04T14:56:29Z,SYNC_RECORD,a,b,r,MAYBE,x\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_csv(p, SystemActivity)


def test_ids_stay_strings(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("case_id,email,requested_at,reported_status,reported_at,audit_at,request_text\n"
                 "007,a@b.com,2026-08-18T09:14:00Z,NOT_STARTED,2026-08-20T16:42:00Z,2026-09-03T11:30:00Z,x\n",
                 encoding="utf-8")
    assert load_csv(p, DeletionRequest)[0].case_id == "007"
