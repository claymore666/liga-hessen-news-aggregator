#!/usr/bin/env bash
# backup_iteration.sh — Snapshot DB, classifier, ChromaDB, and prompt before each iteration.
#
# Usage:
#   ./scripts/backup_iteration.sh                    # Full backup + auto git tag
#   ./scripts/backup_iteration.sh --dry-run           # Show what would happen
#   ./scripts/backup_iteration.sh --no-tag            # Backup without git tag
#   ./scripts/backup_iteration.sh --tag prompts-v5    # Custom tag name
#
# Prerequisites:
#   - SSH access to docker-ai (for prod DB dump)
#   - liga-classifier container running on gpu1
#   - Clean git working tree (uncommitted changes are OK, but warned)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKUP_BASE="$PROJECT_ROOT/backups"
PROCESSOR_PY="$PROJECT_ROOT/news-aggregator/backend/services/processor.py"

# Defaults
DRY_RUN=false
NO_TAG=false
TAG_NAME=""

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)  DRY_RUN=true; shift ;;
        --no-tag)   NO_TAG=true; shift ;;
        --tag)      TAG_NAME="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--dry-run] [--no-tag] [--tag NAME]"
            echo ""
            echo "  --dry-run   Show what would happen without doing it"
            echo "  --no-tag    Skip git tag creation"
            echo "  --tag NAME  Use specific tag name (default: auto-increment prompts-vN)"
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

# ============================================================================
# Helpers
# ============================================================================

info()  { echo "[INFO]  $*"; }
warn()  { echo "[WARN]  $*" >&2; }
error() { echo "[ERROR] $*" >&2; exit 1; }

run_or_dry() {
    if $DRY_RUN; then
        echo "[DRY-RUN] $*"
    else
        eval "$@"
    fi
}

human_size() {
    local file="$1"
    if [[ -f "$file" ]]; then
        du -sh "$file" | cut -f1
    else
        echo "N/A"
    fi
}

# ============================================================================
# Step 1: Validate prerequisites
# ============================================================================

info "Validating prerequisites..."

# Check processor.py exists
[[ -f "$PROCESSOR_PY" ]] || error "Cannot find $PROCESSOR_PY"

# Check git repo
cd "$PROJECT_ROOT"
git rev-parse --git-dir > /dev/null 2>&1 || error "Not in a git repository"

GIT_COMMIT=$(git rev-parse --short HEAD)
GIT_BRANCH=$(git branch --show-current)
info "Git: $GIT_BRANCH @ $GIT_COMMIT"

# Warn about uncommitted changes
if ! git diff --quiet || ! git diff --cached --quiet; then
    warn "Uncommitted changes detected — backup captures committed state only"
fi

# Check SSH to docker-ai
if ! $DRY_RUN; then
    if ! ssh -o ConnectTimeout=5 docker-ai true 2>/dev/null; then
        error "Cannot SSH to docker-ai — needed for prod DB dump"
    fi
    info "SSH to docker-ai: OK"
fi

# Check classifier container
if ! $DRY_RUN; then
    if ! docker ps --format '{{.Names}}' | grep -q liga-classifier; then
        error "liga-classifier container not running"
    fi
    info "liga-classifier container: running"
fi

# ============================================================================
# Step 2: Create backup directory
# ============================================================================

DATE=$(date +%Y-%m-%d)
TIMESTAMP=$(date +%H%M)

# Check if a backup already exists for today
if [[ -d "$BACKUP_BASE/$DATE" ]]; then
    BACKUP_DIR="$BACKUP_BASE/${DATE}-${TIMESTAMP}"
    info "Backup for today already exists, using: $BACKUP_DIR"
else
    BACKUP_DIR="$BACKUP_BASE/$DATE"
fi

info "Backup directory: $BACKUP_DIR"
run_or_dry "mkdir -p '$BACKUP_DIR'"

# ============================================================================
# Step 3: Dump production database
# ============================================================================

info "Dumping production database..."
DB_FILE="$BACKUP_DIR/liga_news_db_PROD.sql.gz"

run_or_dry "ssh docker-ai 'docker exec liga-news-db pg_dump -U liga -d liga_news --no-owner --no-acl' | gzip > '$DB_FILE'"

if ! $DRY_RUN && [[ -f "$DB_FILE" ]]; then
    info "  DB dump: $(human_size "$DB_FILE")"
fi

# ============================================================================
# Step 4: Copy classifier model
# ============================================================================

info "Backing up classifier model..."
MODEL_DIR="$BACKUP_DIR/classifier-model"
run_or_dry "mkdir -p '$MODEL_DIR'"
run_or_dry "docker cp liga-classifier:/app/models/embedding_classifier_nomic-v2.pkl '$MODEL_DIR/'"
run_or_dry "docker cp liga-classifier:/app/models/metrics.json '$MODEL_DIR/' 2>/dev/null || true"

if ! $DRY_RUN && [[ -d "$MODEL_DIR" ]]; then
    info "  Model files: $(du -sh "$MODEL_DIR" | cut -f1)"
fi

# ============================================================================
# Step 5: Snapshot ChromaDB
# ============================================================================

info "Backing up ChromaDB stores..."
CHROMA_FILE="$BACKUP_DIR/classifier-chromadb.tar.gz"

run_or_dry "docker exec liga-classifier tar czf /tmp/chromadb-backup.tar.gz -C /app/data . && docker cp liga-classifier:/tmp/chromadb-backup.tar.gz '$CHROMA_FILE' && docker exec liga-classifier rm /tmp/chromadb-backup.tar.gz"

if ! $DRY_RUN && [[ -f "$CHROMA_FILE" ]]; then
    info "  ChromaDB: $(human_size "$CHROMA_FILE")"
fi

# ============================================================================
# Step 6: Extract and save system prompt
# ============================================================================

info "Extracting ANALYSIS_SYSTEM_PROMPT..."
PROMPT_FILE="$BACKUP_DIR/prompt.txt"

if $DRY_RUN; then
    echo "[DRY-RUN] Extract prompt from $PROCESSOR_PY to $PROMPT_FILE"
else
    python3 -c "
import re, sys
content = open('$PROCESSOR_PY').read()
m = re.search(r'ANALYSIS_SYSTEM_PROMPT = \"\"\"(.*?)\"\"\"', content, re.DOTALL)
if not m:
    print('ERROR: Cannot find ANALYSIS_SYSTEM_PROMPT', file=sys.stderr)
    sys.exit(1)
print(m.group(1).strip())
" > "$PROMPT_FILE"

    PROMPT_HASH=$(sha256sum "$PROMPT_FILE" | cut -d' ' -f1)
    info "  Prompt saved (sha256: ${PROMPT_HASH:0:16}...)"
fi

# ============================================================================
# Step 7: Write manifest
# ============================================================================

info "Writing manifest..."

if $DRY_RUN; then
    echo "[DRY-RUN] Write manifest.json"
else
    PROMPT_HASH=$(sha256sum "$PROMPT_FILE" | cut -d' ' -f1)

    # Compute file sizes
    DB_SIZE=$(stat -c%s "$DB_FILE" 2>/dev/null || echo 0)
    CHROMA_SIZE=$(stat -c%s "$CHROMA_FILE" 2>/dev/null || echo 0)
    MODEL_SIZE=$(stat -c%s "$MODEL_DIR/embedding_classifier_nomic-v2.pkl" 2>/dev/null || echo 0)

    cat > "$BACKUP_DIR/manifest.json" << MANIFEST_EOF
{
  "created_at": "$(date -Iseconds)",
  "git_commit": "$GIT_COMMIT",
  "git_branch": "$GIT_BRANCH",
  "prompt_sha256": "$PROMPT_HASH",
  "files": {
    "db_dump": {"file": "liga_news_db_PROD.sql.gz", "bytes": $DB_SIZE},
    "chromadb": {"file": "classifier-chromadb.tar.gz", "bytes": $CHROMA_SIZE},
    "model": {"file": "classifier-model/embedding_classifier_nomic-v2.pkl", "bytes": $MODEL_SIZE}
  },
  "tag": "${TAG_NAME:-pending}"
}
MANIFEST_EOF

    info "  manifest.json written"
fi

# ============================================================================
# Step 8: Git tag
# ============================================================================

if $NO_TAG; then
    info "Skipping git tag (--no-tag)"
elif $DRY_RUN; then
    if [[ -n "$TAG_NAME" ]]; then
        echo "[DRY-RUN] git tag -a '$TAG_NAME' -m 'Iteration backup ...'"
    else
        # Auto-increment
        LAST_TAG=$(git tag -l 'prompts-v*' --sort=-version:refname | head -1)
        if [[ -z "$LAST_TAG" ]]; then
            NEXT_TAG="prompts-v1"
        else
            LAST_NUM=${LAST_TAG#prompts-v}
            NEXT_TAG="prompts-v$((LAST_NUM + 1))"
        fi
        echo "[DRY-RUN] git tag -a '$NEXT_TAG' -m 'Iteration backup ...'"
    fi
else
    if [[ -z "$TAG_NAME" ]]; then
        # Auto-increment from existing tags
        LAST_TAG=$(git tag -l 'prompts-v*' --sort=-version:refname | head -1)
        if [[ -z "$LAST_TAG" ]]; then
            TAG_NAME="prompts-v1"
        else
            LAST_NUM=${LAST_TAG#prompts-v}
            TAG_NAME="prompts-v$((LAST_NUM + 1))"
        fi
    fi

    # Check tag doesn't already exist
    if git tag -l "$TAG_NAME" | grep -q .; then
        error "Tag '$TAG_NAME' already exists"
    fi

    git tag -a "$TAG_NAME" -m "Iteration backup: $TAG_NAME

Backup: $BACKUP_DIR
Commit: $GIT_COMMIT ($GIT_BRANCH)
Prompt SHA256: ${PROMPT_HASH:-unknown}"

    # Update manifest with tag name
    if [[ -f "$BACKUP_DIR/manifest.json" ]]; then
        sed -i "s/\"tag\": \"pending\"/\"tag\": \"$TAG_NAME\"/" "$BACKUP_DIR/manifest.json"
    fi

    info "Git tag created: $TAG_NAME"
fi

# ============================================================================
# Summary
# ============================================================================

echo ""
echo "=========================================="
echo "  BACKUP COMPLETE"
echo "=========================================="
echo "  Directory: $BACKUP_DIR"
if ! $DRY_RUN; then
    echo "  Total size: $(du -sh "$BACKUP_DIR" | cut -f1)"
fi
echo "  Git commit: $GIT_COMMIT ($GIT_BRANCH)"
if [[ -n "${TAG_NAME:-}" ]] && ! $NO_TAG; then
    echo "  Git tag: $TAG_NAME"
fi
echo "=========================================="
