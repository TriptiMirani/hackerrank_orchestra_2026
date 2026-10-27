# Phase 4 error analysis (samples)

## Score (final)

| metric | value |
|---|---|
| cases | 32 |
| status accuracy | 100.0% |
| step precision | 99.5% (181/182) |
| step recall | 93.8% (181/193) |
| step F1 | 96.5% |
| sequence exact match | 65.6% |
| sequence edit similarity | 91.0% |

| field | accuracy |
|---|---|
| step_no | 86.5% |
| phase | 94.3% |
| system_id | 94.3% |
| resource_id | 93.8% |
| operation | 94.3% |
| state | 94.3% |
| evidence_id | 94.3% |

## Every failing step

| case | side | step | cause |
|---|---|---|---|
| case_03 | missing | VERIFY_RECORD_ANONYMIZED analytics_platform/analytics_9406 [evidence_94] | never-identified |
| case_04 | missing | VERIFY_RECORD_ABSENT account_service/account_004 [evidence_54] | not-in-participant-data |
| case_04 | missing | VERIFY_RECORD_ANONYMIZED analytics_platform/analytics_901 [evidence_56] | never-identified |
| case_18 | missing | VERIFY_RECORD_PRESENT security_service/audit_record_6645 [-] | not-in-participant-data |
| case_20 | missing | VERIFY_RECORD_PRESENT security_service/audit_record_5569 [-] | not-in-participant-data |
| case_21 | missing | VERIFY_RECORD_ANONYMIZED analytics_platform/analytics_5335 [evidence_92] | never-identified |
| case_23 | missing | VERIFY_RECORD_ABSENT account_service/account_410 [-] | tie-attribution |
| case_23 | extra | VERIFY_RECORD_ABSENT account_service/account_9155 [-] | tie-attribution |
| case_25 | missing | VERIFY_RECORD_ANONYMIZED analytics_platform/analytics_1895 [evidence_92] | never-identified |
| case_26 | missing | VERIFY_RECORD_ANONYMIZED analytics_platform/analytics_4566 [evidence_92] | never-identified |
| case_27 | missing | VERIFY_RECORD_ANONYMIZED analytics_platform/analytics_4574 [evidence_92] | never-identified |
| case_28 | missing | VERIFY_RECORD_ANONYMIZED analytics_platform/analytics_6077 [evidence_92] | never-identified |
| case_29 | missing | VERIFY_RECORD_ABSENT account_service/account_7012 [-] | activity-only |

Cause totals: activity-only=1, never-identified=7, not-in-participant-data=3, tie-attribution=2

## Ablations (before/after)

| variant | status | step P | step R | step F1 | exact seq |
|---|---|---|---|---|---|
| final | 100.0% | 99.5% | 93.8% | 96.5% | 65.6% |
| no workflow attribution | 100.0% | 100.0% | 84.5% | 91.6% | 31.2% |
| no late-file retained checks (C13) | 100.0% | 99.4% | 91.2% | 95.1% | 56.2% |
