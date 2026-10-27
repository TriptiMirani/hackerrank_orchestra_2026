# c5_tombstone_leaks_email_hash

The account tombstone keeps user_id (allowed) but still carries the email hash (forbidden by rule_15): exposed PII -> redact. The empty profile snapshot proves the account itself is gone.

Expected: **INCOMPLETE**, 5 steps (see expected_output.csv).
