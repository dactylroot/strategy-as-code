#!/usr/bin/env bash
# Run the web service locally against a project directory.
# Usage: PROJECT_DIR=/path/to/project ./run.sh
#        APP_TITLE="My Product" PROJECT_DIR=... ./run.sh

set -e

if [ -z "$PROJECT_DIR" ]; then
  echo "PROJECT_DIR is required. Usage: PROJECT_DIR=/path/to/project ./run.sh" >&2
  exit 1
fi
export PROJECT_DIR
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
