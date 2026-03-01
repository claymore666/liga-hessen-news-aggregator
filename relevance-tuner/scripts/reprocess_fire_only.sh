#!/bin/bash
# Fire-only reprocessing: sends one reprocess request at a time with a fixed delay.
# The backend processes them asynchronously. Verify results separately afterwards.
#
# Usage:
#   ./reprocess_fire_only.sh <id_file> [start_offset] [delay_secs]

set -euo pipefail

ID_FILE="${1:?Usage: $0 <id_file> [start_offset] [delay_secs]}"
START_OFFSET="${2:-0}"
DELAY="${3:-20}"

mapfile -t IDS < "$ID_FILE"
TOTAL=${#IDS[@]}

echo "=== Fire-Only Reprocessing ==="
echo "Items: $TOTAL, start: $START_OFFSET, delay: ${DELAY}s between items"
echo "Estimated time: $(( (TOTAL - START_OFFSET) * DELAY / 60 ))m"
echo ""

FIRED=0
ERRORS=0
START_TIME=$(date +%s)

for ((i=START_OFFSET; i<TOTAL; i++)); do
    ID="${IDS[$i]}"
    ELAPSED_MIN=$(( ($(date +%s) - START_TIME) / 60 ))

    if [ $FIRED -gt 0 ]; then
        REMAINING=$(( (TOTAL - i) * DELAY / 60 ))
    else
        REMAINING="?"
    fi

    printf "\r[%d/%d] Item %d (%dm elapsed, ~%sm left)   " "$((i+1))" "$TOTAL" "$ID" "$ELAPSED_MIN" "$REMAINING"

    # Fire reprocess
    RESP=$(ssh -n docker-ai "curl -s -X POST http://localhost:8000/api/items/${ID}/reprocess" 2>/dev/null || echo '{"status":"error"}')
    STATUS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','error'))" 2>/dev/null || echo "error")

    if [ "$STATUS" = "started" ]; then
        FIRED=$((FIRED + 1))
    else
        ERRORS=$((ERRORS + 1))
    fi

    # Wait before next item to let LLM process
    sleep "$DELAY"
done

echo ""
echo ""
echo "=== Done ==="
echo "Fired: $FIRED, Errors: $ERRORS"
echo "Time: $(( ($(date +%s) - START_TIME) / 60 ))m"
echo ""
echo "Verify results with:"
echo "  ssh docker-ai \"docker exec liga-news-db psql -U liga -d liga_news -c \\\"SELECT COUNT(*) FROM items WHERE metadata::text LIKE '%Automatische Analyse nicht verf%'\\\"\""
