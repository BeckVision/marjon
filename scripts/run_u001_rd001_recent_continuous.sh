#!/usr/bin/env bash
# Overlap-protected continuous runner for recent-window RD-001 maintenance.
#
# Usage:
#   ./scripts/run_u001_rd001_recent_continuous.sh
#   MARJON_U001_RD001_CONTINUOUS_SLEEP_SECONDS=30 ./scripts/run_u001_rd001_recent_continuous.sh

set -uo pipefail

LOCK_FILE="/tmp/marjon_u001_rd001_recent.lock"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
STATUS_FILE="$LOG_DIR/u001_rd001_recent_runner_status.txt"
SLEEP_SECONDS="${MARJON_U001_RD001_CONTINUOUS_SLEEP_SECONDS:-0}"
ERROR_SLEEP_SECONDS="${MARJON_U001_RD001_CONTINUOUS_ERROR_SLEEP_SECONDS:-300}"
MAX_CYCLES="${MARJON_U001_RD001_CONTINUOUS_MAX_CYCLES:-0}"

mkdir -p "$LOG_DIR"

CURRENT_STATE="starting"
LAST_EXIT_CODE=""
CURRENT_LOG_FILE=""
LAST_LOG_FILE=""
LAST_CYCLE_STARTED_AT=""
LAST_CYCLE_COMPLETED_AT=""
cycle=0

write_status() {
    local tmp_file="${STATUS_FILE}.tmp"
    {
        printf 'pid=%s\n' "$$"
        printf 'state=%s\n' "$CURRENT_STATE"
        printf 'cycle=%s\n' "$cycle"
        printf 'updated_at=%s\n' "$(date -Iseconds)"
        printf 'sleep_seconds=%s\n' "$SLEEP_SECONDS"
        printf 'error_sleep_seconds=%s\n' "$ERROR_SLEEP_SECONDS"
        printf 'max_cycles=%s\n' "$MAX_CYCLES"
        printf 'current_log_file=%s\n' "$CURRENT_LOG_FILE"
        printf 'last_log_file=%s\n' "$LAST_LOG_FILE"
        printf 'last_cycle_started_at=%s\n' "$LAST_CYCLE_STARTED_AT"
        printf 'last_cycle_completed_at=%s\n' "$LAST_CYCLE_COMPLETED_AT"
        printf 'last_exit_code=%s\n' "$LAST_EXIT_CODE"
    } >"$tmp_file"
    mv "$tmp_file" "$STATUS_FILE"
}

cleanup_status() {
    CURRENT_STATE="stopped"
    write_status
}

trap cleanup_status EXIT

exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "$(date -Iseconds) U-001 RD-001 recent runner already running, skipping." >&2
    exit 0
fi

cd "$PROJECT_DIR"
write_status
while true; do
    cycle=$((cycle + 1))
    LOG_FILE="$LOG_DIR/u001_rd001_recent_$(date +%Y%m%d_%H%M%S)_cycle${cycle}.log"
    CURRENT_LOG_FILE="$LOG_FILE"
    LAST_LOG_FILE="$LOG_FILE"
    LAST_CYCLE_STARTED_AT="$(date -Iseconds)"
    CURRENT_STATE="running"
    write_status
    echo "$(date -Iseconds) starting rd001 recent cycle ${cycle}" | tee -a "$LOG_FILE"
    if "$SCRIPT_DIR/manage.sh" run_u001_rd001_recent_cycle "$@" 2>&1 | tee -a "$LOG_FILE"; then
        LAST_EXIT_CODE="0"
        LAST_CYCLE_COMPLETED_AT="$(date -Iseconds)"
        NEXT_SLEEP_SECONDS="$SLEEP_SECONDS"
        CURRENT_STATE="sleeping"
    else
        LAST_EXIT_CODE="$?"
        LAST_CYCLE_COMPLETED_AT="$(date -Iseconds)"
        NEXT_SLEEP_SECONDS="$ERROR_SLEEP_SECONDS"
        CURRENT_STATE="cycle_error"
        echo "$(date -Iseconds) rd001 recent cycle ${cycle} failed, continuing after ${NEXT_SLEEP_SECONDS}s" | tee -a "$LOG_FILE"
    fi
    write_status

    if [[ "$MAX_CYCLES" -gt 0 && "$cycle" -ge "$MAX_CYCLES" ]]; then
        echo "$(date -Iseconds) reached MAX_CYCLES=${MAX_CYCLES}, stopping." | tee -a "$LOG_FILE"
        break
    fi

    echo "$(date -Iseconds) sleeping ${NEXT_SLEEP_SECONDS}s before next cycle" | tee -a "$LOG_FILE"
    sleep "$NEXT_SLEEP_SECONDS"
done
