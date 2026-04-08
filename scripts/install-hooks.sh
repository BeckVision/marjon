#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

chmod +x "$REPO_DIR/.githooks/pre-commit"
git -C "$REPO_DIR" config core.hooksPath .githooks

printf 'Installed git hooks from %s/.githooks\n' "$REPO_DIR"
printf 'core.hooksPath=%s\n' "$(git -C "$REPO_DIR" config --get core.hooksPath)"
