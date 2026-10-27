# c1_sync_recreates_deleted_data

A sync job recreated a search entry after the account deletion. The 404 for the account is real, but the search copy came back and the connector that recreated it is still enabled.

Expected: **INCOMPLETE**, 7 steps (see expected_output.csv).
