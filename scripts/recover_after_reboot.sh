#!/usr/bin/env bash
# Boot-time recovery wrapper for local unattended U-001 automation.
#
# Usage (cron):
#   @reboot /home/beck/Desktop/projects/marjon/scripts/recover_after_reboot.sh
#
# Usage (manual):
#   ./scripts/recover_after_reboot.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/_common.sh"

PROJECT_DIR="$(project_dir)"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/recover_after_reboot_$(date +%Y%m%d_%H%M%S).log"
LOCK_FILE="/tmp/marjon_recover_after_reboot.lock"
DOCKER_WAIT_SECONDS="${MARJON_BOOT_DOCKER_WAIT_SECONDS:-180}"
DB_WAIT_SECONDS="${MARJON_BOOT_DB_WAIT_SECONDS:-180}"
START_RD001_CONTINUOUS="${MARJON_BOOT_START_RD001_CONTINUOUS:-1}"

mkdir -p "$LOG_DIR"

exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "$(date -Iseconds) Reboot recovery already running, skipping." >&2
    exit 0
fi

exec > >(tee -a "$LOG_FILE") 2>&1

cd "$PROJECT_DIR"
ensure_env_file
ensure_venv

echo "$(date -Iseconds) Starting reboot recovery for marjon."

docker_deadline=$((SECONDS + DOCKER_WAIT_SECONDS))
until docker compose up -d db; do
    if (( SECONDS >= docker_deadline )); then
        echo "$(date -Iseconds) Docker did not become ready within ${DOCKER_WAIT_SECONDS}s."
        exit 1
    fi
    echo "$(date -Iseconds) Docker not ready yet, retrying in 5s..."
    sleep 5
done

if ! wait_for_tcp 127.0.0.1 5433 "$DB_WAIT_SECONDS"; then
    echo "$(date -Iseconds) PostgreSQL did not become reachable within ${DB_WAIT_SECONDS}s."
    exit 1
fi

echo "$(date -Iseconds) PostgreSQL is reachable. Running migrations."
"$SCRIPT_DIR/manage.sh" migrate

echo "$(date -Iseconds) Recording reboot recovery and running one immediate U-001 automation tick."
"$SCRIPT_DIR/manage.sh" recover_u001_after_reboot --log-path "$LOG_FILE"

if [[ "$START_RD001_CONTINUOUS" == "1" ]]; then
    echo "$(date -Iseconds) Launching dedicated RD-001 recent continuous runner."
    nohup "$SCRIPT_DIR/run_u001_rd001_recent_continuous.sh" >/dev/null 2>&1 &
fi

echo "$(date -Iseconds) Reboot recovery complete."
