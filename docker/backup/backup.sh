#!/bin/sh
set -eu

DB_FILE="${DB_FILE:-/data/app.db}"
BACKUP_DIR="${BACKUP_DIR:-/backup}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB_FILE" ]; then
  echo "[$(date '+%F %T')] sqlite db not found: $DB_FILE"
  exit 0
fi

TS="$(date '+%Y%m%d_%H%M%S')"
TARGET="$BACKUP_DIR/app_${TS}.db"
TMP_TARGET="$TARGET.tmp"

# Use sqlite online backup for consistency.
sqlite3 "$DB_FILE" ".backup '$TMP_TARGET'"
mv "$TMP_TARGET" "$TARGET"

find "$BACKUP_DIR" -type f -name 'app_*.db' -mtime +"$RETENTION_DAYS" -delete

echo "[$(date '+%F %T')] backup done: $TARGET"
