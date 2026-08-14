#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_PATH:?DATABASE_PATH is required}"
: "${BACKUP_DIR:?BACKUP_DIR is required}"
: "${BACKUP_AGE_RECIPIENT:?BACKUP_AGE_RECIPIENT is required}"

mkdir -p -- "$BACKUP_DIR"
backup_tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "$backup_tmp_dir"' EXIT

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
plain_path="$backup_tmp_dir/job-bot-$timestamp.sqlite3"
encrypted_path="$backup_tmp_dir/job-bot-$timestamp.sqlite3.age"
final_path="$BACKUP_DIR/job-bot-$timestamp.sqlite3.age"

sqlite3 "$DATABASE_PATH" ".backup '$plain_path'"
age --recipient "$BACKUP_AGE_RECIPIENT" --output "$encrypted_path" "$plain_path"
mv -- "$encrypted_path" "$final_path"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'job-bot-*.sqlite3.age' -mtime +7 -delete
