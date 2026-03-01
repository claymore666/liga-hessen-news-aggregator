#!/bin/bash
# Reprocess items in chunks via the news-aggregator API.
# Usage: ./reprocess_chunk.sh <chunk_number>
#   chunk 1 = items 1-120   (~60 min)
#   chunk 2 = items 121-240 (~60 min)
#   chunk 3 = items 241-360 (~60 min)
#   chunk 4 = items 361-480 (~60 min)
#   chunk 5 = items 481-588 (~55 min)
#
# To stop: Ctrl+C (items already submitted will finish processing)
# To resume: just run the same chunk again — already-processed items will be fast

set -e

CHUNK=${1:?Usage: $0 <chunk_number 1-5>}
CHUNK_FILE="/tmp/reprocess_chunk_$(printf '%c' $(echo $((CHUNK + 96)) | awk '{printf "%c", $1}'))"

# Map chunk number to file suffix
case $CHUNK in
    1) CHUNK_FILE="/tmp/reprocess_chunk_aa" ;;
    2) CHUNK_FILE="/tmp/reprocess_chunk_ab" ;;
    3) CHUNK_FILE="/tmp/reprocess_chunk_ac" ;;
    4) CHUNK_FILE="/tmp/reprocess_chunk_ad" ;;
    5) CHUNK_FILE="/tmp/reprocess_chunk_ae" ;;
    *) echo "Invalid chunk number (1-5)"; exit 1 ;;
esac

if [ ! -f "$CHUNK_FILE" ]; then
    echo "Chunk file not found: $CHUNK_FILE"
    echo "Run the ID extraction first."
    exit 1
fi

TOTAL=$(wc -l < "$CHUNK_FILE")
echo "=== Chunk $CHUNK: $TOTAL items ==="
echo "Estimated time: ~$((TOTAL * 25 / 60)) minutes"
echo "Press Ctrl+C to stop (in-flight items will finish)"
echo ""

COUNT=0
ERRORS=0
START=$(date +%s)

while read -r ID; do
    COUNT=$((COUNT + 1))
    ELAPSED=$(( $(date +%s) - START ))
    if [ $COUNT -gt 1 ]; then
        ETA=$(( ELAPSED * TOTAL / (COUNT - 1) - ELAPSED ))
        ETA_MIN=$((ETA / 60))
    else
        ETA_MIN="?"
    fi

    printf "[%d/%d] Item %s (elapsed: %dm, ETA: %sm)... " "$COUNT" "$TOTAL" "$ID" "$((ELAPSED/60))" "$ETA_MIN"

    RESULT=$(ssh -n docker-ai "curl -s -X POST http://localhost:8000/api/items/$ID/reprocess" 2>/dev/null)
    STATUS=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','error'))" 2>/dev/null || echo "error")

    if [ "$STATUS" = "started" ]; then
        echo "queued"
    else
        echo "ERROR: $STATUS"
        ERRORS=$((ERRORS + 1))
    fi

    # Wait for LLM to process before sending next (avoid queue buildup)
    sleep 3

done < "$CHUNK_FILE"

ELAPSED=$(( $(date +%s) - START ))
echo ""
echo "=== Chunk $CHUNK complete ==="
echo "Processed: $COUNT items in $((ELAPSED/60))m"
echo "Errors: $ERRORS"
