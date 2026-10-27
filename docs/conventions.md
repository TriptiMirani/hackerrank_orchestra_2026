# Output conventions inferred from `sample_output.csv`

Each convention has a status:
- **Verified** — checked programmatically over all 32 sample rows, with the checking script named;
- **Observed** — seen in specific cases;
- **Open** — not yet settled.

Nothing here is hand-tuned to a case. The conventions are rules that Phase 3/4 code must implement and the scorer will re-check.

## C1. Canonical step order: Verified (32/32)
- Phases run FINDING → REMEDIATION → VERIFICATION.
- Within a phase, steps follow the brief's operation table order, then ascending `system_id`, then ascending `resource_id`.
- `step_no` starts at 1 and has no gaps.
- **Check:** the ordering script in the Phase 1 log, which sorts by `(phase index, op index, system_id, resource_id)` and gets 32/32 ordered.
- **Proving case:** case_11. Its EXPOSED_PII findings list `analytics_platform` before `support_platform`, while remediation lists REDACT before ANONYMIZE (op order outranks system).

## C2. Evidence citation per status: Verified (32/32)
- **COMPLETE** (10/10): only VERIFICATION steps, and every one cites evidence.
- **INCOMPLETE** (22/22): only FINDING steps cite evidence. REMEDIATION and VERIFICATION steps have `evidence_id = ""`.
- **NOT_APPLICABLE:** `[]`. There is no sample of this; it comes from the brief.

## C3. Which file to cite: Verified (70/70 direct, 5/5 explained)
**Rule:**
1. Consider only files whose **manifest `captured_at` ≤ `audit_at`**.
2. Among their observations of the resource with `observed_at` ≤ `audit_at`, take the latest.
3. Cite that observation's file.

**Why the file-level cutoff matters:** `evidence_02` (exported 09-17) contains a snapshot dated 09-15 12:00. Its inner time is ≤ `audit_at` for case_21–30 (`audit_at` 09-15 12:00), yet those cases cite `evidence_92` (exported 09-15 11:44). Likewise case_09 cites `evidence_93`, not `evidence_06` (exported 09-18). A file created after `audit_at` is not evidence for that case, even if it describes earlier moments.

**The 5 citations with no direct observation:**

| Case | Cited | How the file proves the state |
|---|---|---|
| case_01, case_02, case_04 | `evidence_54` | The resource is missing from both the primary and failover tables, which cover all of `account_service`. That makes it **absence by coverage**. |
| case_31 | `evidence_51` | An empty `customer_profiles` snapshot at 09-15 12:00, so also **absence by coverage**. |
| case_07 | `evidence_58` (PDF) | `support_001` appears in the text ("Support reference CS-4827 / support_001"). Phase 2 text matching produces this fact. |

## C4. The account record is always verified: Verified (32/32)
- Every non-N/A case ends with `VERIFY_RECORD_ABSENT account_service <account>`.
- When the account was found remaining (case_05, 16, 24), it is reported, deleted and verified.
- When it was already gone, only the verification step appears. In COMPLETE cases it cites the evidence of absence (404 or coverage).

## C5. Remediation ↔ finding ↔ verification mapping: Verified (41/41 remediations verified)

| Finding | Remediation | Verification | Count in samples |
|---|---|---|---|
| REPORT_REMAINING_RECORD | DELETE_RECORD | VERIFY_RECORD_ABSENT | 21 |
| REPORT_EXPOSED_PII | REDACT_RECORD | VERIFY_RECORD_REDACTED | 6 |
| REPORT_EXPOSED_PII | ANONYMIZE_RECORD | VERIFY_RECORD_ANONYMIZED | 5 |
| REPORT_ACTIVE_DATA_FLOW | DISABLE_DATA_FLOW | VERIFY_DATA_FLOW_DISABLED | 6 |
| REPORT_RESTORE_RISK | BLOCK_RESTORE | VERIFY_RESTORE_BLOCKED | 3 |

Every remediation has its matching verification step.

## C6. Policy treatment by data category: Observed
- **EXPOSED_PII → ANONYMIZE:** always `analytics_platform` (case_11, 18, 22, 29, 30). Matches rule_07.
- **EXPOSED_PII → REDACT:** `support_platform` (support messages, rule_05: case_11, 18, 19, 29, 30) and `identity_service` tombstones (rule_15: case_32).
- **REMAINING → DELETE:** search entries, exports, accounts, support **attachments**, and backups' source data.
- **Support message vs attachment:**
  - case_07 `support_001` is a PDF attachment, so it is DELETEd.
  - case_13 `support_4218` is `SUPPORT_ATTACHMENT` in `evidence_03`, so it is DELETEd.
  - case_11 `support_864` is `SUPPORT_MESSAGE`, so it is REDACTed.
  - Parsers carry the category from the collection (`support_conversations` / `ticket_attachments`), the handover `record_type`, or the log event (`message_pii_scan` / `attachment_pii_scan`).
- **Open:** case_12 `analytics_9914` is REMAINING → **DELETE**, not ANONYMIZE. Its only evidence is `contact_lookup_completed rows=1 email_opt_in=true`, keyed by an **email hash**. That points to a marketing contact (rule_10 MARKETING_PROFILE, DELETE) stored in the analytics platform. To be settled in Phase 3 by rule, not by case.

## C7. Retained records are verified PRESENT: Verified (24/24 on security_service)
- Every `VERIFY_RECORD_PRESENT` in the samples is a `security_service audit_record_*`: SECURITY_AUDIT_LOG, RETAIN within its retention period, rule_12.
- It appears when an audit record for the subject exists at `audit_at`. Cases 01, 02 and 04–08, 10 have none.
- In COMPLETE cases it cites the file observing the record. In INCOMPLETE cases it is a future check with an empty `evidence_id`.

## C8. Tombstones: Observed (case_31, case_32)
- **case_31:** `tombstone_440` has `name`, `email`, `phone` and `email_hash` all null and keeps `subject_reference`. Result: COMPLETE with `VERIFY_RECORD_REDACTED identity_service tombstone_440`.
- **case_32:** `tombstone_9178` still holds the name, email, phone and hash. Result: `REPORT_EXPOSED_PII` → `REDACT_RECORD` → `VERIFY_RECORD_REDACTED` (rule_15).
- A tombstone is never deleted, and its `subject_reference` is never treated as a violation.

## C9. Data flows and backups: Observed
- **Data flows:** `enabled=true` is REPORT_ACTIVE_DATA_FLOW (case_11, 15, 18, 20, 29, 30). `enabled=false` is `VERIFY_DATA_FLOW_DISABLED` in COMPLETE cases (case_26–28).
- **Backups:** `restore_allowed=true` (or `eligible=true deletion_block=false`, or handover `ACTIVE`) is REPORT_RESTORE_RISK (case_09, 14, 17). `restore_allowed=false` is `VERIFY_RESTORE_BLOCKED` (case_26–28). Backups are never deleted (rule_14 RETAIN).

## C10. Absence evidence: Verified via C3
A resource counts as absent at time T when either:
- an explicit negative readback exists (`404`, `rows=0`, `NOT_FOUND`, `result=miss`); or
- a file that **covers** the collection (`ScopeCoverage`) at T does not contain the resource.

Coverage is only claimed where the source enumerates a collection:
- inventory tables listed in `snapshot_scope`;
- snapshot-bundle collections;
- replica topology tables;
- single-collection JSON exports.

## Open questions for Phase 3
1. **System aliases.** Snapshot bundles use `backup_restore_service`, `privacy_treatment_service`, `support_storage_service` and `legal_service` as `data_store`. The sample answers only use `backup_service`, `support_platform`, etc. We need to know whether these are the same systems under other names or decoys.
2. **case_12's analytics DELETE** (see C6).
3. **Replica drift.** When the primary lacks a record that a replica still has (case_05), the replica copy is the remaining record, cited with the snapshot file.

---
# Added in Phase 3 (each verified against all 32 samples with the verdict comparator)

## C11. Coverage is subject-scoped, except typed database snapshots: Verified
- Snapshot bundles, inventories (`snapshot_scope`), replica payload tables and collection exports are per-subject extracts. An empty collection proves absence **only for subjects the file mentions**.
  - **Proving case:** case_31 cites evidence_51 (about users 541/542) for `account_4857`.
  - **The same file must not** mark case_24's `account_5334` absent, and before this fix it did.
- Typed tables in a point-in-time DB snapshot (`customer_profiles_primary`/`_failover_replica` in evidence_54, `search_documents_primary` in evidence_57) enumerate the whole table.
  - **Proving cases:** case_01/02/04 (accounts absent from evidence_54).

## C12. Replication drift: each copy has its own timeline: Verified (case_05)
- A resource remains if **any** physical copy's latest eligible evidence shows it: the live system, or a failover/read/reporting/regional replica.
- Coverage proves absence only for the copy it enumerates.
- **Proving case:** case_05. `account_005` is gone from the primary (evidence_54 primary table; evidence_90's empty inventory on 09-05), but the failover replica still holds it, so REMAINING [evidence_54].

## C13. Retained records seen only in late files are uncited future checks: Verified (case_09/23/24/29/30)
- A SECURITY_AUDIT_LOG or LEGAL_HOLD record observed ≤ `audit_at`, but only in a file exported after `audit_at` (evidence_02, evidence_06), appears as `VERIFY_RECORD_PRESENT` with empty evidence in INCOMPLETE plans.
- It never sets state, is never cited, and never affects status (C3 still holds).

## C14. Linking order and the workflow link: Verified
1. **strong**: identifiers or lookup key in an eligible file, or a `data_catalog LINK_RESOURCE_IDENTITY` activity;
2. **page**: a weak OCR page subject;
3. **workflow**: `deletion_worker` activity attributed by `app/audit/workflow.py`. Rules:
   - window ±2 h around `reported_at`;
   - the account deletion's `reported_result` agrees with `reported_status`;
   - a resource owned by another user is never taken;
   - constraint propagation, then a deterministic tie-break for accounts only.
- Workflow links reach only already-compliant records (a violation always carries identifiers), so they never change status.

## C15. Category rules beyond `deletion_rules.csv`
- `support_ticket_*`, `support_conversations`, `support_records` and export `tickets` → SUPPORT_MESSAGE (REDACT; case_18, 26–30).
- `ticket_attachments`, `attachment_pii_scan` and handover `SUPPORT_ATTACHMENT` → DELETE (case_13).
- Personal data with no matching rule → DELETE, because the request authorizes deletion and nothing requires retention (case_07 PDF attachment, case_10 legacy contact record).
- `contact_lookup` keyed by email hash with `email_opt_in` → MARKETING_PROFILE (DELETE; case_12 `analytics_9914`).
- `operational_record_history` rows inside `privacy_treatment_service` / `backup_restore_service` / `support_storage_service` are lookups about **other** systems. They are not systems themselves.

## Known unreachable expectations (no participant-visible evidence)
- **Not in any file or in activity:** `audit_record_6645` (case_18) and `audit_record_5569` (case_20).
- **`account_7012` (case_29)** exists only in activity, and its deletion is symmetric with `account_5482` (case_28): same `reported_at`, both FAILED, no identifiers. Attribution is a tie.
- **Anonymized analytics** for case_03/04/21/25–28: never linked to a person in any file; the workflow events are symmetric within their `reported_at` group.

## C16. Found by the validator on the full run (35 COMPLETE cases with an uncited account check)
- **Replica `customer_data_*` snapshots cover the profile store even with zero profile rows.** They are
  subject-scoped (C11), so an empty profile store proves the subject's account is gone, as with an empty
  `customer_profiles` bundle snapshot (case_31). This took the eval validator errors from 35 to 2.
- **Account tie-breaks prefer grounded evidence:** a candidate account read back in an eligible file that mentions
  the subject comes before timing alone. Eval validator errors 2 → 0; eval tie cases 121 → 114; sample step F1
  95.7% → 96.5%; status unchanged.

## C17. Flow/restore settings come from an observation that states them (code review fix #4)
- A data flow is ACTIVE or DISABLED, and a backup is OPEN or BLOCKED, according to the **latest eligible observation that states the setting**:
  - fields: `enabled`, `restore_allowed`, `decision`, `eligible`/`deletion_block`, a handover ACTIVE/BLOCKED/DISABLED;
  - or the record's own screenshot row/card in words, negative words first: "disabled/paused/stopped" and "blocked/locked/legal hold" before "continuous/healthy/enabled" and "archive".
- That observation's file is cited.
- **No statement → UNKNOWN, never assumed compliant.** Proving cases: eval case_39 (evidence_14) and case_55 (evidence_32); see `docs/code-review-2026-10-24.md`.

## C18. Review round 2 decisions
- **Unproven backup/flow setting:** present, tied to the subject, no observation states block/disable → RESTORE_RISK / ACTIVE_FLOW with the full finding → remediation → verification chain. The finding cites the file showing the record exists. 0 instances in this dataset (traced).
- **Unassessable case** (crash, unresolved identity): the run fails (exit 2, no `output.csv`). No row ever claims a status without proof.
- **Names never link alone.** Catalog links trust only the catalog's own user_id.
- **Catalog-only resources with no observation** (`object_19`, `tombstone_17`, `export_1842`) are not linked by alias. The canonical samples (case_07, case_17) exclude them.
