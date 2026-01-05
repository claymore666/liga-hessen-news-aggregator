#!/bin/bash
# Fetch multiple sources and show results
# Usage: ./fetch_sources.sh [source_ids...]
# Example: ./fetch_sources.sh 100 116 137
# Example: ./fetch_sources.sh $(seq 100 110)

TRAINING_MODE="${TRAINING_MODE:-true}"
BASE_URL="${BASE_URL:-http://localhost:8000}"

if [ $# -eq 0 ]; then
    echo "Usage: $0 <source_id> [source_id...]"
    echo "Example: $0 100 116 137"
    echo "Example: $0 \$(seq 100 110)"
    exit 1
fi

echo "Fetching ${#@} sources (training_mode=$TRAINING_MODE)..."
echo "---"

total_items=0
success_count=0
fail_count=0

for id in "$@"; do
    result=$(curl -s -X POST "${BASE_URL}/api/sources/${id}/fetch?training_mode=${TRAINING_MODE}")
    new_items=$(echo "$result" | jq -r '.new_items // empty')
    error=$(echo "$result" | jq -r '.detail // empty')

    if [ -n "$new_items" ]; then
        echo "Source $id: $new_items items"
        total_items=$((total_items + new_items))
        success_count=$((success_count + 1))
    elif [ -n "$error" ]; then
        echo "Source $id: ERROR - $error"
        fail_count=$((fail_count + 1))
    else
        echo "Source $id: 0 items"
        success_count=$((success_count + 1))
    fi
done

echo "---"
echo "Total: $total_items new items from $success_count sources ($fail_count failed)"
