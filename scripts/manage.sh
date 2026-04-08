#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/_common.sh"

PROJECT_DIR="$(project_dir)"

ensure_venv

cd "$PROJECT_DIR"
exec "$(python_bin)" manage.py "$@"
