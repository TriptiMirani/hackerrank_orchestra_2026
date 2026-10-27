"""Profile the dataset: row counts, categorical values, evidence types, and LLM workload.

Usage: python scripts/inventory.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from app.config import DATASET_DIR  # noqa: E402
from app.extract.pdf import page_texts, scanned_page_numbers  # noqa: E402

CSVS = ["deletion_requests", "sample_deletion_requests", "sample_output", "user_identities",
        "user_audit_log", "system_activity", "deletion_rules", "evidence_files", "output"]


def load(name: str) -> pd.DataFrame:
    return pd.read_csv(DATASET_DIR / f"{name}.csv", dtype=str, keep_default_na=False)


def show_counts(title: str, series: pd.Series) -> None:
    print(f"\n{title}")
    for value, n in series.value_counts().sort_index().items():
        print(f"  {n:5d}  {value}")


def main() -> None:
    print("== Row counts")
    frames = {name: load(name) for name in CSVS}
    for name, df in frames.items():
        print(f"  {name + '.csv':32s} {len(df):5d} rows  cols={list(df.columns)}")

    show_counts("== reported_status (deletion_requests)", frames["deletion_requests"]["reported_status"])
    show_counts("== reported_status (sample)", frames["sample_deletion_requests"]["reported_status"])
    show_counts("== deletion_status (sample_output)", frames["sample_output"]["deletion_status"])

    rules = frames["deletion_rules"]
    print("\n== deletion_rules")
    for _, r in rules.iterrows():
        print(f"  {r.rule_id}  {r.data_category:22s} {r.required_action:10s} {r.condition}")

    act = frames["system_activity"]
    show_counts("== system_activity.event_type", act["event_type"])
    show_counts("== system_activity.reported_result", act["reported_result"])
    show_counts("== system_activity source_system -> target_system",
                act["source_system"] + " -> " + act["target_system"])

    show_counts("== user_audit_log.field_name", frames["user_audit_log"]["field_name"])

    ev = frames["evidence_files"]
    ev = ev.assign(ext=ev["file_path"].str.rsplit(".", n=1).str[-1].str.lower(),
                   folder=ev["file_path"].str.split("/").str[1])
    show_counts("== evidence by extension", ev["ext"])
    show_counts("== evidence by folder", ev["folder"])
    show_counts("== evidence by origin", ev["origin"])

    missing = [p for p in ev["file_path"] if not (DATASET_DIR / p).exists()]
    print(f"\n== manifest paths missing on disk: {len(missing)} {missing[:5]}")

    print("\n== LLM workload (images + scanned PDF pages)")
    images = ev[ev["ext"].isin(["png", "jpg", "jpeg", "webp"])]
    print(f"  images: {len(images)}")
    scanned_total = 0
    for _, row in ev[ev["ext"] == "pdf"].iterrows():
        path = DATASET_DIR / row.file_path
        pages = len(page_texts(path))
        scanned = scanned_page_numbers(path)
        scanned_total += len(scanned)
        print(f"  pdf {row.file_id:12s} pages={pages} scanned_pages={scanned}  {row.file_path}")
    print(f"  => LLM units: {len(images)} images + {scanned_total} scanned PDF pages = "
          f"{len(images) + scanned_total}")
    det = Counter(ev[~ev["ext"].isin(["png", "jpg", "jpeg", "webp"])]["ext"])
    print(f"  deterministic files: {sum(det.values())} {dict(sorted(det.items()))}")


if __name__ == "__main__":
    main()
