#!/bin/bash
# Sequential reprocessing script - processes items one at a time via prod API
# Waits for actual LLM completion and verifies results before moving to next item.
#
# Usage:
#   ./reprocess_sequential.sh /tmp/error_items.txt [start_offset]
#
# The script writes progress to /tmp/reprocess_progress.log
# and saves results to /tmp/reprocess_results.jsonl
#
# To stop: Ctrl+C (current item will finish, progress is saved)

set -euo pipefail

ID_FILE="${1:?Usage: $0 <id_file> [start_offset]}"
START_OFFSET="${2:-0}"

PROGRESS_LOG="/tmp/reprocess_progress.log"
RESULTS_FILE="/tmp/reprocess_results.jsonl"
MAX_WAIT=120  # Max seconds to wait for LLM analysis per item
POLL_INTERVAL=5  # Seconds between status checks

# Read IDs into array
mapfile -t IDS < "$ID_FILE"
TOTAL=${#IDS[@]}

echo "=== Sequential Reprocessing ==="
echo "Total items: $TOTAL"
echo "Starting from offset: $START_OFFSET"
echo "Results: $RESULTS_FILE"
echo "Press Ctrl+C to stop (progress is saved)"
echo ""

SUCCESS=0
ERRORS=0
SKIPPED=0
START_TIME=$(date +%s)

for ((i=START_OFFSET; i<TOTAL; i++)); do
    ID="${IDS[$i]}"
    ELAPSED=$(( $(date +%s) - START_TIME ))
    ELAPSED_MIN=$(( ELAPSED / 60 ))

    if [ $SUCCESS -gt 0 ]; then
        RATE=$(echo "scale=1; $ELAPSED / $SUCCESS" | bc 2>/dev/null || echo "?")
        REMAINING=$(echo "scale=0; ($TOTAL - $i) * $RATE / 60" | bc 2>/dev/null || echo "?")
        ETA="ETA: ${REMAINING}m"
    else
        ETA="ETA: ?m"
    fi

    printf "[%d/%d] Item %d (elapsed: %dm, %s)... " "$((i+1))" "$TOTAL" "$ID" "$ELAPSED_MIN" "$ETA"

    # Trigger reprocess
    RESP=$(ssh -n docker-ai "curl -s -X POST http://localhost:8000/api/items/${ID}/reprocess" 2>/dev/null)
    STATUS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "error")

    if [ "$STATUS" != "started" ]; then
        echo "SKIP ($STATUS)"
        SKIPPED=$((SKIPPED + 1))
        echo "$ID SKIP $STATUS" >> "$PROGRESS_LOG"
        continue
    fi

    # Poll until LLM analysis appears (not error default)
    WAITED=0
    RESULT="pending"
    while [ $WAITED -lt $MAX_WAIT ]; do
        sleep $POLL_INTERVAL
        WAITED=$((WAITED + POLL_INTERVAL))

        # Check if item now has real analysis (not error default)
        CHECK=$(ssh -n docker-ai "curl -s http://localhost:8000/api/items/${ID}" 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
meta = data.get('metadata', {})
llm = meta.get('llm_analysis', {})
reasoning = llm.get('reasoning', '')
score = llm.get('relevance_score', 0)
priority = data.get('priority', 'none')
if 'Automatische Analyse nicht verf' in reasoning:
    print('STILL_ERROR')
elif reasoning and len(reasoning) > 30:
    print(f'OK|{priority}|{score}|{reasoning[:80]}')
else:
    print('PENDING')
" 2>/dev/null || echo "CHECK_FAILED")

        if [[ "$CHECK" == OK* ]]; then
            RESULT="$CHECK"
            break
        elif [[ "$CHECK" == "STILL_ERROR" ]]; then
            # Re-trigger might be needed, but let's wait more first
            :
        elif [[ "$CHECK" == "CHECK_FAILED" ]]; then
            :
        fi
    done

    if [[ "$RESULT" == OK* ]]; then
        PRIORITY=$(echo "$RESULT" | cut -d'|' -f2)
        SCORE=$(echo "$RESULT" | cut -d'|' -f3)
        REASON=$(echo "$RESULT" | cut -d'|' -f4-)
        echo "OK (${PRIORITY}, score=${SCORE})"
        SUCCESS=$((SUCCESS + 1))
        echo "{\"id\":$ID,\"status\":\"ok\",\"priority\":\"$PRIORITY\",\"score\":$SCORE}" >> "$RESULTS_FILE"
    else
        echo "TIMEOUT after ${MAX_WAIT}s"
        ERRORS=$((ERRORS + 1))
        echo "{\"id\":$ID,\"status\":\"timeout\"}" >> "$RESULTS_FILE"
    fi

    echo "$ID $RESULT" >> "$PROGRESS_LOG"
done

END_TIME=$(date +%s)
TOTAL_MIN=$(( (END_TIME - START_TIME) / 60 ))

echo ""
echo "=== Complete ==="
echo "Processed: $((SUCCESS + ERRORS + SKIPPED)) / $TOTAL"
echo "Success: $SUCCESS"
echo "Errors: $ERRORS"
echo "Skipped: $SKIPPED"
echo "Time: ${TOTAL_MIN}m"
