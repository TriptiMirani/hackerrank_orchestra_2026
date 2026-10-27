"""Phase 1 proof tooling over the deterministic evidence index (no LLM, no OCR).

  python scripts/parse_report.py --case case_05 [--case case_31 ...]   facts for a sample case's resources
  python scripts/parse_report.py --citations                            check every sample_output citation
  python scripts/parse_report.py --summary                              per-file parse status table
  python scripts/parse_report.py --file evidence_09                     text facts of one OCR/PDF file + its lines
  python scripts/parse_report.py --text-facts                           check OCR/PDF facts vs hand-verified fixture

For --case, the resources come from the case's sample answer: this is a proof that the parsers SEE the
right facts, not a solver. Identity resolution and state-at-audit_at are Phase 3.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import REPO_ROOT  # noqa: E402
from app.ingest.evidence_index import get_index  # noqa: E402
from app.ingest.loaders import load_sample_output, load_sample_requests  # noqa: E402


def fmt_obs(o, audit_at) -> str:
    when = o.observed_at.isoformat().replace("+00:00", "Z") if o.observed_at else "?"
    after = "  AFTER audit_at" if o.observed_at and o.observed_at > audit_at else ""
    ids = ",".join(f"{k}={v}" for k, v in sorted(o.identifiers.items()))
    extra = {k: o.attrs[k] for k in ("enabled", "restore_allowed", "topology", "retention_until", "event",
                                     "direct_identifiers", "marker_found") if k in o.attrs}
    return (f"    {o.file_id:11s} {o.locator:38.38s} {when:20s} present={o.present!s:5s} "
            f"cat={o.data_category or '-':18s} status={o.status_text or '-':24.24s} ids[{ids}] "
            f"{json.dumps(extra) if extra else ''}{after}")


def show_case(idx, case_id: str) -> None:
    req = {r.case_id: r for r in load_sample_requests()}[case_id]
    out = {r.case_id: r for r in load_sample_output()}[case_id]
    steps = json.loads(out.steps_json)
    print(f"\n## {case_id}  expected={out.deletion_status}  email={req.email}  audit_at={req.audit_at:%Y-%m-%dT%H:%MZ}")
    for rid in sorted({s["resource_id"] for s in steps}):
        ops = [f"{s['operation']}[{s['evidence_id'] or '-'}]" for s in steps if s["resource_id"] == rid]
        print(f"  {rid}: expected {' '.join(ops)}")
        for o in sorted(idx.by_resource.get(rid, []), key=lambda o: (o.observed_at is None, o.observed_at)):
            print(fmt_obs(o, req.audit_at))


def check_citations(idx) -> None:
    reqs = {r.case_id: r for r in load_sample_requests()}
    tally = Counter()
    for row in load_sample_output():
        for s in json.loads(row.steps_json):
            if not s["evidence_id"]:
                continue
            req = reqs[row.case_id]
            in_file = [o for o in idx.by_resource.get(s["resource_id"], []) if o.file_id == s["evidence_id"]]
            cap = idx.files[s["evidence_id"]].captured_at
            ok_time = cap <= req.audit_at
            verdict = "FOUND" if in_file else "NOT_IN_FILE"
            tally[verdict] += 1
            tally["captured<=audit_at" if ok_time else "captured>audit_at"] += 1
            states = sorted({(o.present, o.status_text) for o in in_file}, key=str)
            print(f"{row.case_id} {s['operation']:26s} {s['resource_id']:18s} {s['evidence_id']:11s} {verdict:11s} "
                  f"captured<=audit:{ok_time!s:5s} states={states}")
    print("\n", dict(tally))


def summary(idx) -> None:
    print("| file_id | parser | status | observations | coverage | text lines | error |")
    print("|---|---|---|---|---|---|---|")
    for fid, r in sorted(idx.results.items()):
        print(f"| {fid} | {r.parser} | {r.status.value} | {len(r.observations)} | {len(r.coverage)} | "
              f"{len(r.lines)} | {r.error or ''} |")


def show_file(idx, file_id: str) -> None:
    r = idx.results[file_id]
    print(f"\n## {file_id} {r.file_path} parser={r.parser} status={r.status.value} "
          f"observations={len(r.observations)} lines={len(r.lines)}")
    for o in r.observations:
        flags = " INJECTION-TEXT-IGNORED" if o.attrs.get("instruction_like_text_ignored") else ""
        print(f"  {o.resource_id:18s} {o.system_id:20s} cat={o.data_category or '-':18s} [{o.locator}] "
              f"ids={dict(sorted(o.identifiers.items()))}{flags}")
        print(f"      text: {str(o.attrs.get('group_text'))[:150]}")


def check_text_facts(idx) -> None:
    expected = json.loads((REPO_ROOT / "tests/fixtures/text_facts_expected.json").read_text(encoding="utf-8"))
    total = ok = 0
    print("| file_id | status | resources found/expected | extra | link errors |")
    print("|---|---|---|---|---|")
    for fid, want in expected.items():
        if fid.startswith("_"):
            continue
        r = idx.results[fid]
        got: dict[str, list[dict]] = {}
        for o in r.observations:
            got.setdefault(o.resource_id, []).append(o.identifiers)
        link_errors = []
        for rid, ids in want.items():
            total += 1
            # pass if ANY observation of the resource carries all expected identifiers
            if rid in got and any(all(obs.get(k) == v for k, v in ids.items()) for obs in got[rid]):
                ok += 1
            else:
                link_errors.append(rid)
        extra = sorted(set(got) - set(want))
        print(f"| {fid} | {r.status.value} | {len(set(want) & set(got))}/{len(want)} | {', '.join(extra) or '-'} | "
              f"{', '.join(link_errors) or '-'} |")
    print(f"\nresources with correct links: {ok}/{total}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append", default=[])
    ap.add_argument("--citations", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--text-facts", action="store_true")
    args = ap.parse_args()
    idx = get_index()
    if args.summary:
        summary(idx)
    if args.citations:
        check_citations(idx)
    if args.text_facts:
        check_text_facts(idx)
    for f in args.file:
        show_file(idx, f)
    for c in args.case:
        show_case(idx, c)


if __name__ == "__main__":
    main()
