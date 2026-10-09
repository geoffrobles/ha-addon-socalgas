#!/bin/bash

# Hours between successful runs, from the add-on option (default 6).
INTERVAL_HOURS=$(python3 -c '
import json
try:
    h = int(json.load(open("/data/options.json")).get("update_interval_hours") or 6)
except Exception:
    h = 6
print(min(max(h, 1), 24))
')
SUCCESS_INTERVAL=$((INTERVAL_HOURS * 3600))
echo "Update interval: ${INTERVAL_HOURS}h"

# Backoff after a failed run. Capped at a few attempts so a persistent
# failure (bad password, bot block) doesn't hammer the login endpoint.
RETRY_DELAYS=(300 900 3600)

failures=0

while true
do
    echo "Running SoCalGas sync..."

    python3 /app/socalgas_api_slim.py
    rc=$?

    if [ "$rc" -eq 0 ]; then
        failures=0
        delay=$SUCCESS_INTERVAL
    elif [ "$failures" -lt "${#RETRY_DELAYS[@]}" ]; then
        delay=${RETRY_DELAYS[$failures]}
        failures=$((failures + 1))
        echo "Sync failed (exit $rc), retry $failures/${#RETRY_DELAYS[@]}."
    else
        failures=0
        delay=$SUCCESS_INTERVAL
        echo "Sync failed (exit $rc), retries exhausted, resuming normal schedule."
    fi

    echo "Sleeping $((delay / 60)) minutes..."
    sleep "$delay"
done
