#!/usr/bin/env bash
# Install the program-strategy Claude Code skill.
#
# Usage:
#   ./scripts/install-skill.sh              # global (~/.claude/skills/)
#   ./scripts/install-skill.sh /path/to/project   # per-project

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SKILL_SRC="$REPO_DIR/.claude/skills/program-strategy"

if [ -n "$1" ]; then
  TARGET_DIR="$1/.claude/skills"
  SCOPE="project ($1)"
else
  TARGET_DIR="$HOME/.claude/skills"
  SCOPE="global"
fi

mkdir -p "$TARGET_DIR"
LINK="$TARGET_DIR/program-strategy"

if [ -e "$LINK" ] || [ -L "$LINK" ]; then
  rm -rf "$LINK"
fi

ln -sf "$SKILL_SRC" "$LINK"
echo "program-strategy skill installed ($SCOPE)"
echo "  $LINK -> $SKILL_SRC"
