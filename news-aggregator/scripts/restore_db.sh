#!/bin/bash
# Liga News Database Restore Script
# Restores SQLite database from a backup file
#
# Usage:
#   ./restore_db.sh backup.db.gz              # Restore to local database
#   ./restore_db.sh backup.db.gz --docker     # Restore to Docker container
#   ./restore_db.sh backup.db.gz --no-verify  # Skip integrity verification
#
# Environment variables:
#   DB_PATH       - Target database path (default: ./backend/data/liga_news.db)
#   DOCKER_CONTAINER - Container name (default: liga-news-backend)

set -euo pipefail

# Configuration defaults
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

DB_PATH="${DB_PATH:-${PROJECT_ROOT}/backend/data/liga_news.db}"
DOCKER_CONTAINER="${DOCKER_CONTAINER:-liga-news-backend}"
DOCKER_DB_PATH="/app/data/liga_news.db"
USE_DOCKER=false
VERIFY=true

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Show usage
usage() {
    echo "Usage: $0 <backup_file> [OPTIONS]"
    echo ""
    echo "Arguments:"
    echo "  backup_file        Path to backup file (.db or .db.gz)"
    echo ""
    echo "Options:"
    echo "  --docker           Restore to Docker container"
    echo "  --no-verify        Skip integrity verification"
    echo "  -h, --help         Show this help message"
    exit 0
}

# Check arguments
if [[ $# -lt 1 ]]; then
    log_error "Missing backup file argument"
    usage
fi

BACKUP_FILE="$1"
shift

# Parse options
while [[ $# -gt 0 ]]; do
    case $1 in
        --docker)
            USE_DOCKER=true
            shift
            ;;
        --no-verify)
            VERIFY=false
            shift
            ;;
        --help|-h)
            usage
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "============================================"
echo "  Liga News Database Restore"
echo "============================================"
echo ""
log_info "Backup file: $BACKUP_FILE"

# Check backup exists
if [[ ! -f "$BACKUP_FILE" ]]; then
    log_error "Backup file not found: $BACKUP_FILE"
    exit 1
fi

# Create temp directory for work
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

# Decompress if needed
RESTORE_FILE="$BACKUP_FILE"
if [[ "$BACKUP_FILE" == *.gz ]]; then
    log_info "Decompressing backup..."
    RESTORE_FILE="${TEMP_DIR}/restore.db"
    gunzip -c "$BACKUP_FILE" > "$RESTORE_FILE"
fi

# Verify integrity
if $VERIFY; then
    log_info "Verifying backup integrity..."
    INTEGRITY_CHECK=$(sqlite3 "$RESTORE_FILE" "PRAGMA integrity_check;" 2>&1)
    if [[ "$INTEGRITY_CHECK" == "ok" ]]; then
        log_info "Integrity check: PASSED"
    else
        log_error "Integrity check: FAILED"
        log_error "$INTEGRITY_CHECK"
        exit 1
    fi

    # Show backup stats
    ITEM_COUNT=$(sqlite3 "$RESTORE_FILE" "SELECT COUNT(*) FROM items;" 2>/dev/null || echo "0")
    SOURCE_COUNT=$(sqlite3 "$RESTORE_FILE" "SELECT COUNT(*) FROM sources;" 2>/dev/null || echo "0")
    log_info "Backup contains: $ITEM_COUNT items, $SOURCE_COUNT sources"
fi

# Restore based on mode
if $USE_DOCKER; then
    log_info "Restoring to Docker container: $DOCKER_CONTAINER"

    # Check if container exists
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${DOCKER_CONTAINER}$"; then
        log_error "Container '$DOCKER_CONTAINER' does not exist"
        exit 1
    fi

    # Check if container is running
    CONTAINER_RUNNING=false
    if docker ps --format '{{.Names}}' | grep -q "^${DOCKER_CONTAINER}$"; then
        CONTAINER_RUNNING=true
    fi

    if $CONTAINER_RUNNING; then
        log_info "Stopping container..."
        docker stop "$DOCKER_CONTAINER"
    fi

    # Copy backup to container
    log_info "Copying backup to container..."
    docker cp "$RESTORE_FILE" "${DOCKER_CONTAINER}:${DOCKER_DB_PATH}"

    if $CONTAINER_RUNNING; then
        log_info "Starting container..."
        docker start "$DOCKER_CONTAINER"

        # Wait for container to be healthy
        log_info "Waiting for container to be ready..."
        for i in {1..30}; do
            if docker exec "$DOCKER_CONTAINER" python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" 2>/dev/null; then
                log_info "Container is ready"
                break
            fi
            sleep 1
        done
    fi
else
    log_info "Restoring to local database: $DB_PATH"

    # Create backup of current database
    if [[ -f "$DB_PATH" ]]; then
        CURRENT_BACKUP="${DB_PATH}.pre-restore.$(date +%Y%m%d_%H%M%S)"
        log_info "Backing up current database to: $CURRENT_BACKUP"
        cp "$DB_PATH" "$CURRENT_BACKUP"
    fi

    # Ensure target directory exists
    mkdir -p "$(dirname "$DB_PATH")"

    # Copy restored database
    cp "$RESTORE_FILE" "$DB_PATH"
    log_info "Database restored successfully"
fi

# Verify restoration
if $VERIFY; then
    log_info "Verifying restoration..."
    if $USE_DOCKER; then
        if docker ps --format '{{.Names}}' | grep -q "^${DOCKER_CONTAINER}$"; then
            RESTORED_COUNT=$(docker exec "$DOCKER_CONTAINER" sqlite3 "$DOCKER_DB_PATH" "SELECT COUNT(*) FROM items;" 2>/dev/null || echo "0")
            log_info "Restored database contains: $RESTORED_COUNT items"
        fi
    else
        RESTORED_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM items;" 2>/dev/null || echo "0")
        log_info "Restored database contains: $RESTORED_COUNT items"
    fi
fi

echo ""
echo "============================================"
log_info "Restore complete!"
echo "============================================"
