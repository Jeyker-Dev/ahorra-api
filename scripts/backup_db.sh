#!/usr/bin/env bash
# Dumps the production PostgreSQL database to ~/ahorra-api/backups on the VPS,
# keeping the 14 newest dumps. Runs from cron and from scripts/deploy.sh.
# Usage: backup_db.sh [label]

set -euo pipefail

cd "$HOME/ahorra-api"
label="${1:-cron}"
backup_dir="$HOME/ahorra-api/backups"
mkdir -p "$backup_dir"
backup_file="$backup_dir/backup_$(date -u +%Y%m%d_%H%M%S)_$label.sql.gz"
backup_tmp="$backup_file.tmp"

if ! docker compose -f compose.prod.yml exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' </dev/null | gzip > "$backup_tmp"; then
    rm -f "$backup_tmp"
    echo "Database backup failed." >&2
    exit 1
fi
mv "$backup_tmp" "$backup_file"

# shellcheck disable=SC2012
(ls -1t "$backup_dir"/backup_*.sql.gz 2>/dev/null || true) | tail -n +15 | xargs -r -d '\n' rm -f
echo "$backup_file"
