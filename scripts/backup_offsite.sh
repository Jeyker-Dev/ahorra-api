#!/usr/bin/env bash
# Sends the production PostgreSQL dump and .env to the ahorra-api S3 bucket with restic,
# then applies retention (7 daily, 4 weekly, 6 monthly). Runs on the VPS from cron.
# Credentials and the restic password live in ~/.config/backup/ahorra-api.env (mode 600):
# AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, RESTIC_REPOSITORY and RESTIC_PASSWORD.

set -euo pipefail

app_dir="$HOME/ahorra-api"
set -a
# shellcheck disable=SC1091
. "$HOME/.config/backup/ahorra-api.env"
set +a

cd "$app_dir"
docker compose -f compose.prod.yml exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' </dev/null \
    | restic backup --quiet --tag db --stdin --stdin-filename ahorra-api.sql
restic backup --quiet --tag env "$app_dir/.env"
restic forget --quiet --prune --group-by tags --keep-daily 7 --keep-weekly 4 --keep-monthly 6
echo "$(date -u +%FT%TZ) off-site backup ok"
