#!/usr/bin/env bash
# Reverses install.sh: removes the token-optimizer PreToolUse hook entry from
# ~/.claude/settings.json, and optionally removes the installed gate script
# and its state directory.
set -euo pipefail

PY=""
for candidate in python3 python py; do
  if command -v "$candidate" >/dev/null 2>&1 \
     && "$candidate" -c "import sys; sys.exit(0 if sys.version_info[0]==3 else 1)" >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "Error: no working Python 3 interpreter found (tried python3, python, py)." >&2
  echo "On Windows, a 'python3' that prints 'Python was not found' is the Microsoft" >&2
  echo "Store stub, not an interpreter. Disable it under Settings > Apps >" >&2
  echo "Advanced app settings > App execution aliases." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
SETTINGS_FILE="$CLAUDE_DIR/settings.json"
GATE_FILE="$CLAUDE_DIR/hooks/token_optimizer_gate.py"
STATE_DIR="$CLAUDE_DIR/.token-optimizer-state"
AGENTS_DIR="$CLAUDE_DIR/agents"
REPO_AGENTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd 2>/dev/null)/agents"

"$PY" "$SCRIPT_DIR/_unmerge_settings.py" "$SETTINGS_FILE"

if [ -f "$GATE_FILE" ]; then
  rm -f "$GATE_FILE"
  echo "Removed $GATE_FILE"
else
  echo "No gate script found at $GATE_FILE (nothing to remove)."
fi

# Remove only the agent files we are confident WE installed: still
# byte-identical to what is shipped in the repo's agents/ directory. A file
# that differs means it either pre-existed or the user has since customized
# it, so it is left alone.
if [ -d "$REPO_AGENTS_DIR" ]; then
  for name in scout runner builder; do
    src="$REPO_AGENTS_DIR/$name.md"
    dest="$AGENTS_DIR/$name.md"
    if [ -f "$dest" ]; then
      if [ -f "$src" ] && cmp -s "$src" "$dest"; then
        rm -f "$dest"
        echo "Removed ~/.claude/agents/$name.md"
      else
        echo "~/.claude/agents/$name.md differs from what was installed - leaving it alone (looks customized or pre-existing)."
      fi
    fi
  done
else
  echo "Warning: could not find the repo's agents/ directory to compare against." >&2
  echo "If you want them gone, manually check/remove ~/.claude/agents/{scout,runner,builder}.md yourself." >&2
fi

if [ -d "$STATE_DIR" ]; then
  if [ -t 0 ]; then
    read -r -p "Also remove the state directory at $STATE_DIR? It only holds harmless per-session counters. [y/N] " REPLY || REPLY=""
  else
    REPLY=""
    echo "Not running interactively, so leaving the state directory at $STATE_DIR in place."
    echo "Remove it yourself with: rm -rf \"$STATE_DIR\""
  fi
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
