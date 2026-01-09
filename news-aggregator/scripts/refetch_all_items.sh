#!/bin/bash
# Refetch all items to extract article content from links
# Usage: ./scripts/refetch_all_items.sh [--dry-run]

set -e

API_URL="http://localhost:8000"
DRY_RUN=false
PARALLEL=5  # Number of parallel requests

if [[ "$1" == "--dry-run" ]]; then
    DRY_RUN=true
    echo "DRY RUN - no actual refetching"
fi

echo "=== Refetch All Items ==="
echo "This will extract article content from linked URLs for all items."
echo ""

# Get total count
TOTAL=$(curl -s "$API_URL/api/items?page_size=1&relevant_only=false" | jq '.total')
echo "Total items: $TOTAL"

# Get all item IDs
echo "Fetching item IDs..."
ALL_IDS=""
PAGE=1
while true; do
    IDS=$(curl -s "$API_URL/api/items?page=$PAGE&page_size=100&relevant_only=false" | jq -r '.items[].id')
    if [[ -z "$IDS" ]]; then
        break
    fi
    ALL_IDS="$ALL_IDS $IDS"
    COUNT=$(echo "$ALL_IDS" | wc -w)
    echo "  Page $PAGE: $COUNT total IDs"
    if [[ $COUNT -ge $TOTAL ]]; then
        break
    fi
    PAGE=$((PAGE + 1))
    if [[ $PAGE -gt 50 ]]; then
        echo "Safety limit reached"
        break
    fi
done

# Convert to array
IDS_ARRAY=($ALL_IDS)
TOTAL_IDS=${#IDS_ARRAY[@]}
echo ""
echo "Total IDs to process: $TOTAL_IDS"

if $DRY_RUN; then
    echo "Would refetch $TOTAL_IDS items"
    exit 0
fi

echo ""
echo "Starting refetch (this will take a while)..."
echo "Progress will be logged to /tmp/refetch_progress.log"
echo ""

# Process items
SUCCESS=0
FAILED=0
SKIPPED=0

for i in "${!IDS_ARRAY[@]}"; do
    ID=${IDS_ARRAY[$i]}
    NUM=$((i + 1))

    # Progress every 50 items
    if [[ $((NUM % 50)) -eq 0 || $NUM -eq 1 ]]; then
        echo "[$NUM/$TOTAL_IDS] Processing item $ID..."
    fi

    # Refetch the item
    RESULT=$(curl -s -X POST "$API_URL/api/items/$ID/refetch" 2>&1)

    if echo "$RESULT" | grep -q '"status":\s*"started"\|"success"'; then
        SUCCESS=$((SUCCESS + 1))
    elif echo "$RESULT" | grep -q '"status":\s*"skipped"'; then
        SKIPPED=$((SKIPPED + 1))
    else
        FAILED=$((FAILED + 1))
        echo "  Failed item $ID: $(echo "$RESULT" | jq -r '.detail // .error // "unknown"' 2>/dev/null | head -c 60)"
    fi

    # Small delay to avoid overwhelming the server
    sleep 0.2
done

echo ""
echo "=== COMPLETE ==="
echo "Success: $SUCCESS"
echo "Skipped: $SKIPPED"
echo "Failed: $FAILED"
echo ""
echo "Now run: curl -X POST '$API_URL/api/items/reprocess?limit=1000&force=true'"
