"""Phase 3 proof: deletion_status vs sample_output, and per-case identity/timeline dumps.

  python scripts/status_check.py                 # status accuracy table on the 32 samples
  python scripts/status_check.py --show case_09  # identity set + timeline + verdicts for a case
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audit.status import all_requests, assess_case, build_context  # noqa: E402
from app.ingest.loaders import load_sample_output, load_sample_requests  # noqa: E402


def show(a, expected_steps) -> None:
    i = a.identity
    print(f"\n## {a.case_id} -> {a.status}   intent={a.intent.intent.value} ({a.intent.reason[:50]})")
    if i:
        print(f"  identity {i.user_id} via {i.matched_via}: emails={i.emails} phones={i.phones} names={i.names}")
        print(f"           hashes={[h[:12] + '..' for h in i.email_hashes]}")
    exp = {s["resource_id"]: s["operation"] for s in expected_steps if s["phase"] != "REMEDIATION"}
    for it in a.items:
        r = it.resource
        print(f"  {r.resource_id:18s} {r.system_id:20s} cat={it.category:18s} {it.treatment:9s} -> {it.verdict.value:18s}"
              f" state={r.state:7s} as_of={r.as_of:%m-%d %H:%M} ev={r.evidence_id:11s} link={it.link}:{it.link_detail[:45]}"
              f"{' exposed=' + ','.join(it.exposed) if it.exposed else ''}   expected={exp.get(r.resource_id, '-')}"
              if r.as_of else
              f"  {r.resource_id:18s} {r.system_id:20s} cat={it.category:18s} {it.treatment:9s} -> {it.verdict.value:18s}"
              f" state={r.state} link={it.link}:{it.link_detail[:45]}   expected={exp.get(r.resource_id, '-')}")
    missing = sorted(set(exp) - {it.resource.resource_id for it in a.items})
    if missing:
        print(f"  NOT LINKED (expected): {missing}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", nargs="*", default=[])
    args = ap.parse_args()
    ctx = build_context(all_requests())
    expected = {o.case_id: o for o in load_sample_output()}
    ok = 0
    rows = []
    for req in load_sample_requests():
        a = assess_case(req, ctx)
        exp = expected[req.case_id].deletion_status
        ok += a.status == exp
        rows.append((req.case_id, exp, a.status, a))
    print("| case | expected | predicted | match |\n|---|---|---|---|")
    for cid, exp, got, _ in rows:
        print(f"| {cid} | {exp} | {got} | {'yes' if exp == got else '**NO**'} |")
    print(f"\nstatus accuracy: {ok}/{len(rows)} = {ok / len(rows):.1%}")
    for cid, _exp, _got, a in rows:
        if cid in args.show:
            show(a, json.loads(expected[cid].steps_json))


if __name__ == "__main__":
    main()
