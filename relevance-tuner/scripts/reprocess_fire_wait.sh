#!/bin/bash
# Fire-and-wait reprocessing: sends one item, waits for LLM to finish, then next.
# No polling — just waits a fixed time based on observed LLM processing speed.
#
# Usage:
#   ./reprocess_fire_wait.sh <id_file> [start_offset]

set -euo pipefail

ID_FILE="${1:?Usage: $0 <id_file> [start_offset]}"
START_OFFSET="${2:-0}"

RESULTS_FILE="/tmp/reprocess_results.jsonl"
WAIT_SECS=20  # LLM takes ~10-20s with thinking mode; 20s gives buffer

mapfile -t IDS < "$ID_FILE"
TOTAL=${#IDS[@]}

echo "=== Fire-and-Wait Reprocessing ==="
echo "Items: $TOTAL, starting from: $START_OFFSET, wait: ${WAIT_SECS}s"
echo ""

SUCCESS=0
ERRORS=0
START_TIME=$(date +%s)

for ((i=START_OFFSET; i<TOTAL; i++)); do
    ID="${IDS[$i]}"
    ELAPSED=$(( $(date +%s) - START_TIME ))
    ELAPSED_MIN=$(( ELAPSED / 60 ))

    if [ $SUCCESS -gt 0 ]; then
        REMAINING=$(( (TOTAL - i) * ELAPSED / SUCCESS / 60 ))
        ETA="${REMAINING}m"
    else
        ETA="?m"
    fi

    printf "[%d/%d] Item %d (%dm elapsed, ETA %s)... " "$((i+1))" "$TOTAL" "$ID" "$ELAPSED_MIN" "$ETA"

    # Fire reprocess
    RESP=$(ssh -n docker-ai "curl -s -X POST http://localhost:8000/api/items/${ID}/reprocess" 2>/dev/null)
    STATUS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','error'))" 2>/dev/null || echo "error")

    if [ "$STATUS" != "started" ]; then
        echo "SKIP ($STATUS)"
        ERRORS=$((ERRORS + 1))
        continue
    fi

    # Wait for LLM to finish
    sleep "$WAIT_SECS"

    # Quick check result
    RESULT=$(ssh -n docker-ai "curl -s http://localhost:8000/api/items/${ID}" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
m = d.get('metadata',{}).get('llm_analysis',{})
r = m.get('reasoning','')
if 'Automatische Analyse nicht verf' in r:
    print('ERROR')
else:
    print(f\"{d.get('priority','?')}|{m.get('relevance_score',0)}\")
" 2>/dev/null || echo "CHECK_FAIL")

    if [[ "$RESULT" == "ERROR" ]]; then
        # Not done yet, wait more
        sleep 10
        RESULT=$(ssh -n docker-ai "curl -s http://localhost:8000/api/items/${ID}" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
m = d.get('metadata',{}).get('llm_analysis',{})
r = m.get('reasoning','')
if 'Automatische Analyse nicht verf' in r:
    print('ERROR')
else:
    print(f\"{d.get('priority','?')}|{m.get('relevance_score',0)}\")
" 2>/dev/null || echo "CHECK_FAIL")
    fi

    if [[ "$RESULT" != "ERROR" && "$RESULT" != "CHECK_FAIL" ]]; then
        PRI=$(echo "$RESULT" | cut -d'|' -f1)
        SCORE=$(echo "$RESULT" | cut -d'|' -f2)
        echo "OK ($PRI, $SCORE)"
        SUCCESS=$((SUCCESS + 1))
        echo "{\"id\":$ID,\"priority\":\"$PRI\",\"score\":$SCORE}" >> "$RESULTS_FILE"
    else
        echo "FAIL"
        ERRORS=$((ERRORS + 1))
        echo "{\"id\":$ID,\"status\":\"fail\"}" >> "$RESULTS_FILE"
    fi
done

TOTAL_MIN=$(( ($(date +%s) - START_TIME) / 60 ))
echo ""
echo "=== Done: ${SUCCESS} OK, ${ERRORS} errors in ${TOTAL_MIN}m ==="
