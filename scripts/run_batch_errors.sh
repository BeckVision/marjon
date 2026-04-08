#!/usr/bin/env bash
# Targeted retry wrapper for RD-001 error rows.
# Reuses the main guarded batch script and lock.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MAX_COINS="${MARJON_U001_RD001_ERROR_MAX_COINS:-10}"

"$SCRIPT_DIR/run_batch.sh" \
    --status-filter error \
    --max-coins "$MAX_COINS" \
    "$@"
