#!/usr/bin/env bash
# Targeted retry wrapper for old RD-001 partial rows via Helius.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MAX_COINS="${MARJON_U001_RD001_PARTIAL_HIST_MAX_COINS:-5}"

"$SCRIPT_DIR/run_batch.sh" \
    --status-filter partial \
    --source helius \
    --max-coins "$MAX_COINS" \
    "$@"
