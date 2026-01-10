#!/bin/bash
# Liga News Database Backup Script
# Creates timestamped SQLite backups with compression and rotation
#
# Usage:
#   ./backup_db.sh                     # Backup local database
#   ./backup_db.sh --docker            # Backup from Docker container
#   ./backup_db.sh --remote user@host:/path  # Copy backup to remote
#   ./backup_db.sh --keep 14           # Keep 14 backups instead of default 7
#
# Environment variables:
#   BACKUP_DIR    - Backup directory (default: ./backups)
#   DB_PATH       - Local database path (default: ./backend/data/liga_news.db)
#   DOCKER_CONTAINER - Container name (default: liga-news-backend)

set -euo pipefail

# Configuration defaults
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/backups}"
DB_PATH="${DB_PATH:-${PROJECT_ROOT}/backend/data/liga_news.db}"
DOCKER_CONTAINER="${DOCKER_CONTAINER:-liga-news-backend}"
DOCKER_DB_PATH="/app/data/liga_news.db"
KEEP_BACKUPS=7
REMOTE_TARGET=""
USE_DOCKER=false

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --docker)
            USE_DOCKER=true
            shift
            ;;
        --remote)
            REMOTE_TARGET="$2"
            shift 2
            ;;
        --keep)
            KEEP_BACKUPS="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --docker           Backup from Docker container"
            echo "  --remote HOST      Copy backup to remote (user@host:/path)"
            echo "  --keep N           Keep N most recent backups (default: 7)"
            echo "  -h, --help         Show this help message"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Generate filename with timestamp
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="liga_news_${TIMESTAMP}.db"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_FILE}"

echo "============================================"
echo "  Liga News Database Backup"
echo "============================================"
echo ""
log_info "Timestamp: $TIMESTAMP"
log_info "Backup directory: $BACKUP_DIR"

# Create backup
if $USE_DOCKER; then
    log_info "Backing up from Docker container: $DOCKER_CONTAINER"

    # Check if container is running
    if ! docker ps --format '{{.Names}}' | grep -q "^${DOCKER_CONTAINER}$"; then
        log_error "Container '$DOCKER_CONTAINER' is not running"
        exit 1
    fi

    # Use sqlite3 backup command for consistent snapshot
    docker exec "$DOCKER_CONTAINER" sqlite3 "$DOCKER_DB_PATH" ".backup '/tmp/backup.db'"
    docker cp "${DOCKER_CONTAINER}:/tmp/backup.db" "$BACKUP_PATH"
    docker exec "$DOCKER_CONTAINER" rm /tmp/backup.db
else
    log_info "Backing up local file: $DB_PATH"

    # Check if database exists
    if [[ ! -f "$DB_PATH" ]]; then
        log_error "Database file not found: $DB_PATH"
        exit 1
    fi

    # Check if sqlite3 is available
    if ! command -v sqlite3 &> /dev/null; then
        log_error "sqlite3 command not found. Please install sqlite3."
        exit 1
    fi

    sqlite3 "$DB_PATH" ".backup '${BACKUP_PATH}'"
fi

# Verify backup was created
if [[ ! -f "$BACKUP_PATH" ]]; then
    log_error "Backup file was not created"
    exit 1
fi

# Verify backup integrity
log_info "Verifying backup integrity..."
INTEGRITY_CHECK=$(sqlite3 "$BACKUP_PATH" "PRAGMA integrity_check;" 2>&1)
if [[ "$INTEGRITY_CHECK" == "ok" ]]; then
    log_info "Integrity check: PASSED"
else
    log_error "Integrity check: FAILED"
    log_error "$INTEGRITY_CHECK"
    rm -f "$BACKUP_PATH"
    exit 1
fi

# Get item count for verification
ITEM_COUNT=$(sqlite3 "$BACKUP_PATH" "SELECT COUNT(*) FROM items;" 2>/dev/null || echo "0")
SOURCE_COUNT=$(sqlite3 "$BACKUP_PATH" "SELECT COUNT(*) FROM sources;" 2>/dev/null || echo "0")
log_info "Backup contains: $ITEM_COUNT items, $SOURCE_COUNT sources"

# Compress backup
log_info "Compressing backup..."
gzip -9 "$BACKUP_PATH"
BACKUP_PATH="${BACKUP_PATH}.gz"

# Report sizes
UNCOMPRESSED_SIZE=$(gzip -l "$BACKUP_PATH" | tail -1 | awk '{print $2}')
COMPRESSED_SIZE=$(stat -c%s "$BACKUP_PATH" 2>/dev/null || stat -f%z "$BACKUP_PATH")
RATIO=$(echo "scale=1; 100 - ($COMPRESSED_SIZE * 100 / $UNCOMPRESSED_SIZE)" | bc 2>/dev/null || echo "N/A")

log_info "Uncompressed: $(numfmt --to=iec $UNCOMPRESSED_SIZE 2>/dev/null || echo "${UNCOMPRESSED_SIZE} bytes")"
log_info "Compressed: $(numfmt --to=iec $COMPRESSED_SIZE 2>/dev/null || echo "${COMPRESSED_SIZE} bytes") (${RATIO}% reduction)"

# Copy to remote if specified
if [[ -n "$REMOTE_TARGET" ]]; then
    log_info "Copying to remote: $REMOTE_TARGET"
    if rsync -avz "$BACKUP_PATH" "$REMOTE_TARGET/"; then
        log_info "Remote copy successful"
    else
        log_warn "Remote copy failed"
    fi
fi

# Rotate old backups
log_info "Rotating old backups (keeping $KEEP_BACKUPS)..."
DELETED=0
while IFS= read -r OLD_BACKUP; do
    rm -v "$OLD_BACKUP" 2>/dev/null && ((DELETED++)) || true
done < <(ls -t "${BACKUP_DIR}"/liga_news_*.db.gz 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)))

if [[ $DELETED -gt 0 ]]; then
    log_info "Deleted $DELETED old backup(s)"
fi

echo ""
echo "============================================"
log_info "Backup complete!"
log_info "File: $BACKUP_PATH"
echo "============================================"
