#!/usr/bin/env bash
# Install the program-strategy skill for Claude Code or OpenCode.
#
# Usage:
#   ./scripts/install-skill.sh                          # Claude Code, global (~/.claude/skills/)
#   ./scripts/install-skill.sh /path/to/project         # Claude Code, per-project
#   ./scripts/install-skill.sh --opencode               # OpenCode, global (~/.config/opencode/skills/)
#   ./scripts/install-skill.sh --opencode /path/to/project   # OpenCode, per-project (.opencode/skills/)
#
# OpenCode also reads .claude/skills and ~/.claude/skills, so a Claude Code
# install is picked up by OpenCode too; --opencode is for people who do not
# want to put anything under .claude.

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SKILL_SRC="$REPO_DIR/.claude/skills/program-strategy"

TOOL="claude"
if [ "$1" = "--opencode" ]; then
  TOOL="opencode"
  shift
fi

if [ -n "$1" ]; then
  [ "$TOOL" = "opencode" ] && TARGET_DIR="$1/.opencode/skills" || TARGET_DIR="$1/.claude/skills"
  SCOPE="$TOOL, project ($1)"
else
  [ "$TOOL" = "opencode" ] && TARGET_DIR="$HOME/.config/opencode/skills" || TARGET_DIR="$HOME/.claude/skills"
  SCOPE="$TOOL, global"
fi

mkdir -p "$TARGET_DIR"
LINK="$TARGET_DIR/program-strategy"

if [ -e "$LINK" ] || [ -L "$LINK" ]; then
  rm -rf "$LINK"
fi

ln -sf "$SKILL_SRC" "$LINK"
echo "program-strategy skill installed ($SCOPE)"
echo "  $LINK -> $SKILL_SRC"
