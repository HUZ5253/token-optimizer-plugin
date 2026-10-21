#!/usr/bin/env bash
# Standalone fallback installer for token-optimizer.
#
# Safely merges a PreToolUse hook entry into ~/.claude/settings.json without
# overwriting any of the user's existing settings. Idempotent: running this
# more than once will not create duplicate hook entries.
set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required but was not found on PATH." >&2
  echo "Install Python 3 and re-run this script." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
HOOKS_DIR="$CLAUDE_DIR/hooks"
SETTINGS_FILE="$CLAUDE_DIR/settings.json"
GATE_SRC="$SCRIPT_DIR/token_optimizer_gate.py"
GATE_DEST="$HOOKS_DIR/token_optimizer_gate.py"

mkdir -p "$CLAUDE_DIR" "$HOOKS_DIR"

if [ ! -f "$GATE_SRC" ]; then
  echo "Error: could not find gate script at $GATE_SRC" >&2
  exit 1
fi

cp "$GATE_SRC" "$GATE_DEST"
chmod +x "$GATE_DEST"

python3 "$SCRIPT_DIR/_merge_settings.py" "$SETTINGS_FILE"
