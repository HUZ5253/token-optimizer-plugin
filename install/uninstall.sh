#!/usr/bin/env bash
# Reverses install.sh: removes the token-optimizer PreToolUse hook entry from
# ~/.claude/settings.json, and optionally removes the installed gate script
# and its state directory.
set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required but was not found on PATH." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
SETTINGS_FILE="$CLAUDE_DIR/settings.json"
GATE_FILE="$CLAUDE_DIR/hooks/token_optimizer_gate.py"
STATE_DIR="$CLAUDE_DIR/.token-optimizer-state"

python3 "$SCRIPT_DIR/_unmerge_settings.py" "$SETTINGS_FILE"

if [ -f "$GATE_FILE" ]; then
  rm -f "$GATE_FILE"
  echo "Removed $GATE_FILE"
else
  echo "No gate script found at $GATE_FILE (nothing to remove)."
fi

if [ -d "$STATE_DIR" ]; then
  read -r -p "Also remove the state directory at $STATE_DIR? It only holds harmless per-session counters. [y/N] " REPLY
  case "$REPLY" in
    [yY][eE][sS]|[yY])
      rm -rf "$STATE_DIR"
      echo "Removed $STATE_DIR"
      ;;
    *)
      echo "Leaving $STATE_DIR in place."
      ;;
  esac
fi

echo ""
echo "token-optimizer uninstalled. This only takes effect in NEW Claude Code sessions."
