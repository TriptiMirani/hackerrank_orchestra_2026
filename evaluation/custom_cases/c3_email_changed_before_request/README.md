# c3_email_changed_before_request

The request uses the OLD email; the account is keyed by the new one and an analytics profile still holds the old one. Identity must resolve through user_audit_log, and both emails must be searched.

Expected: **INCOMPLETE**, 5 steps (see expected_output.csv).
