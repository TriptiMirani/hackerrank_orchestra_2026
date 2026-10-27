# Phase 2: Screenshot and PDF text → facts

## What
- **`app/extract/lexicon.py`:** every known identifier value (current + audit-log history: emails, phones, names, email hashes), mapped to user_ids.
- **`app/extract/textfacts.py`:** turns OCR lines and PDF lines into `Observation`s by grounded matching.
- **Router and index integration:** images are OCR'd from cache and then converted to facts; PDFs are converted per page. An OCR failure becomes `PARSE_FAILED`.
- **Proof tooling:**
  - `scripts/parse_report.py --file <id>` shows one file's facts next to the text they came from;
  - `--text-facts` checks them against `tests/fixtures/text_facts_expected.json` (hand-verified).

## Why
The 11 screenshots and 4 PDFs hold facts that no structured file repeats. For example, case_07's `support_001` exists only in a PDF. Phase 0 showed the local vision LLM is blind in this install. So Phase 2 had to get facts out of text deterministically, without letting OCR noise create identifiers.

## How
1. **Grouping:**
   - **Screenshots:** first split into regions, then group the lines in each region. The regions are:
     - main area: visual rows (boxes whose vertical centres overlap);
     - detail-panel card: one group;
     - panel activity list: rows.
     All 11 consoles have a right panel with an "Activity" heading, which gives the boundary.
   - **PDFs:** one group per page.
2. **Resource IDs:** a strict prefix regex (`account_`, `search_`, `data_flow_`, `audit_record_`, `marketing_`, `legal_hold_`, …). Its boundaries were tuned on the real OCR output.
3. **Identifiers in the same group:**
   - emails, `user_NN` and 64-hex hashes are taken literally;
   - names and phones are taken **only** when they equal a known value. A name is compared without spaces or accents; a phone by digits.
4. **One `Observation` per resource ID per group:**
   - `present=True`, `status_text="shown"`, observed at the manifest `captured_at`;
   - the category comes from the prefix when unambiguous;
   - the group text and the page-level `page_user_ref`/`page_email` are kept in `attrs`.

## Decisions & tradeoffs
- **Row-level linking**, not page-level. Page co-occurrence would link every record on a screen to every person on it; the search console shows a decoy (`user_9842`, "Aditi Jain") in its activity list. Page identifiers are kept only as weak `attrs`.
- **Grounding gate.** On evidence_60, "Soo-jin Park" and her phone match no known identity, so they are not taken. Only her literal email is.
- **Text never changes state.** Nothing in a screenshot or PDF can mark a resource absent. Instruction-like phrases are flagged (`instruction_like_text_ignored`) and otherwise have no effect (hard rule 7).
- **Layout heuristic.** The "Activity"-heading region split is specific to this console template. It falls back to plain rows when the heading is missing.

## Problems found on real data, and their fixes
| Problem | Fix |
|---|---|
| Table row absorbed panel text at the same height (search_1874 got "Gabriel Silva") | Region split at the panel boundary |
| `lucia-torres@…` matched the name "Lucia Torres" | Remove emails before name matching |
| `customer_export_2026-02-10.zip` → `export_2026` | No `_`/letter before an ID; no `-digit` after it |
| `3export_1259` (row badge merged in), `marketing_001-` (`·` read as `-`) | Allow a leading digit; allow a trailing `-` unless a digit follows |

## How to verify it yourself (Git Bash)
```bash
cd ~/Downloads/hackerrank-orchestrate-october26
.venv/Scripts/python.exe -m pytest -q                                   # 61 passed, 1 skipped
.venv/Scripts/python.exe scripts/parse_report.py --text-facts           # 26/26 resources linked correctly
.venv/Scripts/python.exe scripts/parse_report.py --file evidence_09 --file evidence_32
.venv/Scripts/python.exe scripts/parse_report.py --citations | tail -1  # 71 FOUND + 4 absence-by-coverage
```

## Known limitations
- Panel-card grouping depends on rows being less than 45 px apart. A sparser panel would split its title from its fields.
- `evidence_42` (a user_17 reference page) and `evidence_27` (legal register rows keyed by `ORD-…`/`user_NN`) contain no resource IDs, so they yield text lines but no observations.
- Activity-feed claims ("backup_9286 restored · Completed") are kept as text. Interpreting them is Phase 3.
