#!/usr/bin/env bash
# Deploys ahorra-api to the VPS.
#
# Runs the checks locally, then over SSH:
#   1. Updates the git checkout on the VPS (~/ahorra-api/src) and builds ahorra-api:<sha>.
#   2. Ensures PostgreSQL is healthy before touching the running API.
#   3. Backs up PostgreSQL to ~/ahorra-api/backups (14 newest kept).
#   4. Runs Alembic migrations in a one-off container with the new image.
#   5. Restarts api and scheduler with the new image and installs the backup cron jobs
#      (local dump at 03:35, off-site restic backup at 03:45, VPS time).
#   6. Prunes old images, keeping the previous one for rollback.
#
# Usage: scripts/deploy.sh [--skip-checks]
#
# Settings (environment variables, or a gitignored .deploy.env at the repo root):
#   AHORRA_DEPLOY_HOST    SSH destination, e.g. "deploy@1.2.3.4" or a ~/.ssh/config alias (required)
#   AHORRA_DEPLOY_BRANCH  Branch to deploy (default: main)
#   AHORRA_REPO_URL       Repository to clone on the VPS (default: public GitHub HTTPS URL)
#
# One-time VPS setup: Docker and restic installed, the shared edge proxy running (it owns
# the `edge` network) with a site for the API, ~/ahorra-api/.env (see docs/areas/infrastructure.md)
# and ~/.config/backup/ahorra-api.env for the off-site backup.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ -f .deploy.env ]]; then
    # shellcheck disable=SC1091
    source .deploy.env
fi

host="${AHORRA_DEPLOY_HOST:-}"
branch="${AHORRA_DEPLOY_BRANCH:-main}"
repo_url="${AHORRA_REPO_URL:-https://github.com/Jeyker-Dev/ahorra-api.git}"
skip_checks=false

for arg in "$@"; do
    case "$arg" in
        --skip-checks) skip_checks=true ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

if [[ -z "$host" ]]; then
    echo "AHORRA_DEPLOY_HOST is empty. Set it in the environment or in .deploy.env." >&2
    exit 1
fi

git fetch --quiet origin "$branch"
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse "origin/$branch")" ]]; then
    echo "Warning: local HEAD differs from origin/$branch. The VPS deploys origin/$branch, not your local checkout." >&2
fi

if [[ "$skip_checks" == false ]]; then
    echo "==> Running ruff, mypy and unit tests"
    uv run ruff format --check .
    uv run ruff check .
    uv run mypy
    uv run pytest tests/unit -q
fi

echo "==> Deploying origin/$branch to $host"

# shellcheck disable=SC2029  # client-side expansion is intended here
ssh "$host" "BRANCH=$(printf %q "$branch") REPO_URL=$(printf %q "$repo_url") bash -s" <<'REMOTE'
set -euo pipefail

# Wrapped in a function so bash parses the whole script before docker compose exec
# can consume the remaining lines from stdin.
main() {
    cd ~/ahorra-api

    if [[ ! -f .env ]]; then
        echo "~/ahorra-api/.env is missing." >&2
        exit 1
    fi

    if [[ ! -d src/.git ]]; then
        git clone --quiet "$REPO_URL" src
    fi
    git -C src fetch --quiet origin "$BRANCH"
    git -C src reset --quiet --hard "origin/$BRANCH"

    sha="$(git -C src rev-parse --short HEAD)"
    echo "==> Building ahorra-api:$sha"

    cp src/compose.prod.yml compose.prod.yml
    cp src/scripts/backup_db.sh backup_db.sh
    cp src/scripts/backup_offsite.sh backup_offsite.sh
    chmod 700 backup_db.sh backup_offsite.sh

    docker build -t "ahorra-api:$sha" -t ahorra-api:latest src
    export APP_IMAGE="ahorra-api:$sha"

    echo "==> Ensuring PostgreSQL is healthy"
    docker compose -f compose.prod.yml up -d --wait db

    echo "==> Backing up database"
    ./backup_db.sh "$sha"

    echo "==> Running migrations with ahorra-api:$sha"
    docker compose -f compose.prod.yml run --rm --no-deps -T api alembic upgrade head </dev/null

    previous_image="$(docker compose -f compose.prod.yml ps -q api | xargs -r docker inspect --format '{{.Config.Image}}' 2>/dev/null || true)"
    previous_tag="${previous_image#ahorra-api:}"

    echo "==> Starting production stack"
    docker compose -f compose.prod.yml up -d --wait --remove-orphans

    install_cron() {
        local marker="$1" line="$2"
        if ! crontab -l 2>/dev/null | grep -qF "$marker"; then
            echo "==> Installing cron job: $line"
            (crontab -l 2>/dev/null || true; echo "$line") | crontab -
        fi
    }
    install_cron "ahorra-api/backup_db.sh" \
        "35 3 * * * $HOME/ahorra-api/backup_db.sh >> $HOME/ahorra-api/backups/cron.log 2>&1"
    install_cron "ahorra-api/backup_offsite.sh" \
        "45 3 * * * $HOME/ahorra-api/backup_offsite.sh >> $HOME/ahorra-api/backups/offsite.log 2>&1"

    keep_pattern="latest|$sha"
    if [[ -n "$previous_tag" && "$previous_tag" != "$sha" && "$previous_tag" != "latest" ]]; then
        keep_pattern="latest|$sha|$previous_tag"
        echo "==> Previous image kept for rollback: ahorra-api:$previous_tag"
    fi
    docker images ahorra-api --format '{{.Tag}}' \
        | grep -vxE "$keep_pattern" \
        | xargs -r -I{} docker rmi "ahorra-api:{}" >/dev/null 2>&1 || true
    docker image prune -f >/dev/null

    echo "==> Deployed ahorra-api:$sha"
}

main
REMOTE
