#!/usr/bin/env bash
# Run the web service locally against a project directory.
# Usage: PROJECT_DIR=/path/to/project ./run.sh
#        APP_TITLE="My Product" PROJECT_DIR=... ./run.sh

set -e

export PROJECT_DIR="${PROJECT_DIR:-/Users/cory/Workspace/renewals}"
export APP_TITLE="${APP_TITLE:-strategy-as-code}"
PORT="${PORT:-8765}"

echo "Starting strategy-as-code service"
echo "  Project: $PROJECT_DIR"
echo "  URL:     http://localhost:$PORT"
echo ""

PYENV_VERSION=3.12.8 python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --reload
