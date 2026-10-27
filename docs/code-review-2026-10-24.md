# Code review: 2026-10-24 (after Phase 5)

## Review
- **How it ran:** the developer started `/code-review` in Claude Code, and it ran as a background agent over the uncommitted working tree.
- **Output:** 10 ranked findings plus 4 minor ones.
- **Process note:** the review agent reported running one read-only `git diff`, which breaks CLAUDE.md hard rule 1 (no git). Nothing was changed by it. All fixes below were made without git.

## Findings and fixes
Each finding has a regression test in `tests/unit/test_review_fixes.py`. Each test encodes the reviewer's failure scenario. For #6/#7 the old behaviour was also shown failing before the fix.

| # | Finding (reviewer) | Severity / reach in this dataset | Fix | Test |
|---|---|---|---|---|
| 1 | An unresolvable request email raised from `assess_case`, so one case could abort the 518-case run (hard rule 9) | Latent: all 518 emails resolve | `UnresolvedIdentity` becomes a state: COMPLETE `[]` plus an `IDENTITY_UNRESOLVED` note. `run_all` also guards every case (`case_errors`) and lists both in the run record | `test_1_unresolved_identity_returns_a_state` |
| 2 | `_select` kept only REMAINING accounts, so an EXPOSED account next to a workflow "primary" account was dropped | Latent: no `account_service` record is EXPOSED here | Every account violation is kept. The "one primary account" rule applies only to compliant or unknown accounts | `test_2_exposed_account_is_kept_next_to_primary` |
| 3 | A COMPLETE case could emit an uncited `VERIFY_RECORD_ABSENT` for an UNKNOWN account | 0 occurrences after C16 | The planner omits COMPLETE verifications with no evidence, rather than emit an unproven claim | `test_3_complete_plan_skips_uncited_records` |
| 4 | When `flow_active`/`restore_open` returned None ("evidence does not say"), the result was treated as compliant (disabled/blocked) | **Real, 2 eval cases** | Verdict comes from the latest eligible observation that *states* the setting (`latest_signal`); no statement → UNKNOWN; the citation is the file that states it. Follow-up: screenshot rows state settings in words, so the record's own OCR row/card is read with narrow keyword sets (negative words first) | `test_4_…`, `test_4b_…` |
| 5 | Non-deletion requests competed for workflow events | **Real, 1 eval case** (case_41 lost its anonymized analytics record) | Non-deletion requests may claim only their own `CLASSIFY_REQUEST`; deletion requests never claim one; both are excluded from section-2 propagation | `test_5_non_deletion_request_does_not_compete` |
| 6 | The `still (need\|use\|want)` marker always won ("I still want my account deleted" → not deletion) | Latent: the eval texts do not trigger it | The marker requires a keep-object (it / the account / my order history) and no deletion verb later in the sentence | `test_6_7_intent_edge_cases` |
| 7 | "remove my email from your marketing lists" → deletion | Latent | Removal from a marketing/mailing/newsletter list is a strong non-deletion marker | `test_6_7_intent_edge_cases` |
| 8 | `ambiguous_intent` was counted after the LLM/fallback replaced it, so it was always 0 | Reporting bug (0 is correct here by luck) | `CaseAssessment.rule_intent` keeps the rule result, and the run counts from it | `test_8_rule_intent_survives_fallback` |
| 9 | The rollback through post-`audit_at` changes kept the current email's hash when no `email_hash` audit row existed | Latent: no change after any `audit_at` | If the current email did not exist at `audit_at`, its hash is dropped | `test_9_post_audit_email_hash_is_excluded` |
| 10 | The run did not report cases depending on failed extractions (hard rule 9) | 0 failed files here | The run record lists `failed_extractions` and `cases_depending_on_failed_extractions` (cases for which such a file was eligible evidence) | checked in the run record (empty lists) |

### Minor findings, also fixed
- `Identity.matches` rebuilt its normalized sets on every call → computed once (`model_post_init`).
- `run_all.case_sort_key` duplicated `validate_output.natural_key` → now reuses it.
- `rules.exposed_identifiers` hardcoded the ANONYMIZE categories → now decides by the rule's treatment.
- `validate_output` crashed on a short CSV row → now reports `missing steps_json column`.

## Measured effect
| | Before fixes | After fixes |
|---|---|---|
| Unit tests | 82 passed | 96 passed (+14 regression tests) |
| Samples | status 100%, F1 96.5%, exact 65.6% | unchanged |
| Custom cases | 7/7 | 7/7 |
| Eval statuses | 162 COMPLETE / 346 INCOMPLETE / 10 N/A | 160 / 348 / 10 |
| Eval validator | 0 errors, 1 warning | 0 errors, 1 warning |

Eval cases that changed:

| Case | Before | After |
|---|---|---|
| case_39 | COMPLETE: `VERIFY_DATA_FLOW_DISABLED data_flow_1121 [evidence_14]`, but the screenshot shows the flow "Healthy · Continuous sync" | INCOMPLETE: `REPORT_ACTIVE_DATA_FLOW [evidence_14]` → disable → verify |
| case_55 | COMPLETE: `VERIFY_RESTORE_BLOCKED backup_9287 [evidence_32]`, but the screenshot shows a stored, verified archive with no block | INCOMPLETE: `REPORT_RESTORE_RISK [evidence_32]` → block → verify |
| case_41 | 5 verification steps | 6: gained `VERIFY_RECORD_ANONYMIZED analytics_6830 [evidence_03]` (fix #5) |

Later evidence agrees with both new findings (`connector_heartbeat enabled=true`; `restore_candidate_evaluated eligible=true deletion_block=false`, both 09-15). That evidence is not used: the verdicts rest only on the screenshots, which are the eligible files.

## Remaining open
- 114 account tie-breaks (unchanged).
- case_61 is COMPLETE `[]`: no eligible evidence mentions that subject.

---
# Review round 2 (after the round-1 fixes)

## Review
- **How it ran:** the developer started `/code-review` again. This time the agent did not run git and reviewed the files directly.
- **Output:** 10 findings. The developer had them assessed by an advisory "council", whose plan was followed:
  1. #1/#2 fail the run instead of writing a bad row;
  2. trace the UNKNOWN settings before deciding #3, and apply #4 before #3;
  3. land #6 on its own, with link counts;
  4. search the whole repo for #10;
  5. diff statuses after every fix.

## First step: trace every UNKNOWN backup/flow setting
- **Result:** 0 backup or flow settings were UNKNOWN. The round-1 screenshot-text fix had already resolved them.
- **The 5 UNKNOWN items were all catalog-only resources with no observation:** `object_19` (case_07, case_61), `tombstone_17` (case_17, case_46), `export_1842` (case_33).
- **Aliases were checked too.** Their catalog aliases (`delivery_confirmation_19.pdf`, `user_17`, `NST-2026-0718-1842`) do appear in screenshots or PDFs, so alias linking was considered. It would add steps for `object_19` to case_07 and for `tombstone_17` to case_17, and **neither is in those cases' canonical sample answers**. So the samples say these records are rightly dropped.
- **Decision:** they are not linked by alias. They are listed in the run record (`unresolved_records`) for transparency.

## Findings, fixes, and measured status changes (each applied and diffed in this order)
| # | Finding | Fix | Status changes | Step changes | Test |
|---|---|---|---|---|---|
| 4 | `deletion_block=false` without `eligible` read as blocked | block true or eligible false → blocked; block false or eligible true → open | 0 | 0 | `test_r2_4_…` |
| 3 | an UNKNOWN backup/flow setting was dropped, so the case could be COMPLETE | present, tied to the subject, setting unproven → RESTORE_RISK / ACTIVE_FLOW with the full chain (finding cites the file showing the record exists → BLOCK_RESTORE/DISABLE_DATA_FLOW → VERIFY_…); burden of proof on the block (rule_14) | 0 (traced: no instance) | 0 | `test_4_…` (full chain + citation) |
| 1, 2 | a crashed case / unresolved identity became COMPLETE `[]` | status ERROR (not a valid output status); `run_all` exits with code 2 and does **not** write `output.csv`, listing the cases; the validator rejects ERROR | 0 (no instance) | 0 | `test_1_…` |
| 5 | screen keywords matched substrings ("unblocked", "no legal hold", "not stopped") | whole-word match; a match directly preceded by no/not/never/non is ignored | 0 | 0 | `test_r2_5_…` (parameterized) |
| 7 | strong limitation markers won even when negated or contrasted away | a marker preceded by "no longer want (you) to"/"don't want to", or followed by a contrast (but/however/though/yet) that turns to deletion, does not count | 0 (eval still 508/10, samples 32/32) | 0 | `test_r2_7_…` (parameterized) |
| 8 | an AMBIGUOUS request skipped stored LLM decisions when no LLM was configured; failures not reported | LLM intent decisions persisted in `data/intent_decisions.json` and read first (CLI and Space agree); fallbacks are `source="fallback"` and listed in the run record (`cases_intent_fallback`) | 0 | 0 | `test_r2_8_…` |
| 9 | the EXTRACTION_FAILED summary line counted as a model call | `LedgerEntry.summary=True` for bookkeeping lines; excluded from `llm_calls` and from the usage report's call counts | 0 | 0 | `test_r2_9_…` |
| 6 | a name alone could link a record (lookup/catalog keys); a catalog key could override the catalog's own user_id | `_identifier_hit`: strong identifiers (email, user_ref, email_hash, phone) link; a name never links alone (`NAME_LINK_POLICY="strict"`); catalog links trust only their own user_id | 0 | 0 | `test_r2_6_…` |
| 10 | "CLAUDE.md still says Gemini / requires a rate limiter" | **false positive.** A whole-repo search finds Gemini only in organizer files naming other tools (AGENTS.md, agent_trace_instructions.md) and the decision-log entry recording the switch. No code, report or evaluation doc mentions it | — | — | — |

**#6 measured separately.** Links over all 550 requests were identical for policies any / unique / strict: 1,290 strong (email 687, user_ref 411, lookup_key 180, catalog 10, phone 1, email_hash 1), 356 workflow, 123 late-file. No link in this dataset rests on a name alone. Sample scores were unchanged.

## Measured effect of round 2
- **Unit tests:** 96 → 110 passed.
- **Samples:** unchanged (status 100%, F1 96.5%, exact 65.6%). **Custom cases:** 7/7.
- **Eval:** the output with all round-2 fixes is **byte-identical** to the round-1 `output.csv` (sha256 `75b7d470…`). Every round-2 fix is protection against inputs this dataset does not contain. The run record now also carries `cases_intent_fallback` and `unresolved_records` (the 5 catalog-only records above).

## Process note
- Several edits in this round were made through shell heredocs. One of them wrote literal backspace (0x08) control characters into a regex in `flows.py`, where `\b` was intended.
- It was caught because the negation test failed and the Edit tool could not match the text.
- The lines were rewritten. All `.py` sources were then scanned: none contain control characters.
