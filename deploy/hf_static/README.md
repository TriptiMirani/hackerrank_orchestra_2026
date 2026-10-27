---
title: Northstar Deletion Audit
emoji: 🔍
colorFrom: indigo
colorTo: gray
sdk: static
pinned: false
---

# Northstar deletion-audit case viewer (static)

A read-only, static viewer for the deterministic deletion audit ("Ghost Data Still Exists"). For each of the 518 requests (plus the 32 samples, shown with their answer key), it shows:
- the resolved identifiers;
- every linked resource's state at `audit_at`, with the evidence file that proves it;
- the policy verdict;
- the submitted `steps_json`.

`cases.json` is exported by `scripts/export_ui_data.py` from the same code that produced `output.csv`. The eval steps are verified identical row for row. No model, dataset or secret runs here. All data is synthetic.
