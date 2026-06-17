#!/usr/bin/env bash
# Launch the strategy-as-code web UI pointed at the current project directory.
# Usage: run-ui.sh [project_dir]
#   project_dir defaults to the current working directory.

set -e

# Resolve the repo root from this script's real path (skill dir is a symlink)
SCRIPT_REAL="$(realpath "$0")"
REPO_ROOT="$(cd "$(dirname "$SCRIPT_REAL")/../../../.." && pwd)"

export PROJECT_DIR="${1:-$(pwd)}"
export APP_TITLE="${APP_TITLE:-strategy-as-code}"
PORT="${PORT:-8765}"

echo "Starting strategy-as-code UI"
echo "  Project: $PROJECT_DIR"
echo "  URL:     http://localhost:$PORT"
echo ""

cd "$REPO_ROOT"
PYENV_VERSION=3.12.8 python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --reload
