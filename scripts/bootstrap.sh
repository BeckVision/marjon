#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/_common.sh"

PROJECT_DIR="$(project_dir)"

cd "$PROJECT_DIR"

ensure_env_file
ensure_venv
"$(pip_bin)" install -r requirements.txt
docker compose up -d db
"$SCRIPT_DIR/manage.sh" migrate

printf 'Bootstrap complete.\n'
printf 'Python: %s\n' "$(python_bin)"
printf 'Database: docker compose service db is up.\n'
