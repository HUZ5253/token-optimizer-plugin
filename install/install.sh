#!/usr/bin/env bash
# Standalone fallback installer for token-optimizer.
#
# Safely merges a PreToolUse hook entry into ~/.claude/settings.json without
# overwriting any of the user's existing settings. Idempotent: running this
# more than once will not create duplicate hook entries.
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
HOOKS_DIR="$CLAUDE_DIR/hooks"
AGENTS_DIR="$CLAUDE_DIR/agents"
SETTINGS_FILE="$CLAUDE_DIR/settings.json"
GATE_SRC="$SCRIPT_DIR/token_optimizer_gate.py"
GATE_DEST="$HOOKS_DIR/token_optimizer_gate.py"
REPO_AGENTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/agents"

mkdir -p "$CLAUDE_DIR" "$HOOKS_DIR"

if [ ! -f "$GATE_SRC" ]; then
  echo "Error: could not find gate script at $GATE_SRC" >&2
  exit 1
fi

cp "$GATE_SRC" "$GATE_DEST"
chmod +x "$GATE_DEST"

"$PY" "$SCRIPT_DIR/_merge_settings.py" "$SETTINGS_FILE" "$PY"

# Non-destructively install the three starter agents. A pre-existing file at
# the destination (possibly a user's own, more capable, same-named agent) is
# never overwritten.
mkdir -p "$AGENTS_DIR"

AGENT_INSTALL_SUMMARY=()

install_agent() {
  local name="$1"
  local tools_hint="$2"
  local src="$REPO_AGENTS_DIR/$name.md"
  local dest="$AGENTS_DIR/$name.md"

  if [ ! -f "$src" ]; then
    echo "Warning: could not find agent source at $src - skipping." >&2
    AGENT_INSTALL_SUMMARY+=("$name.md: SKIPPED (source missing)")
    return
  fi

  if [ -f "$dest" ]; then
    echo "~/.claude/agents/$name.md already exists - leaving it as-is. If it doesn't grant $tools_hint access, delegation may not work as expected."
    AGENT_INSTALL_SUMMARY+=("$name.md: left alone (already existed)")
  else
    cp "$src" "$dest"
    echo "Installed ~/.claude/agents/$name.md"
    AGENT_INSTALL_SUMMARY+=("$name.md: installed")
  fi
}

install_agent "scout" "Read/Grep/Glob"
install_agent "runner" "Bash/Read"
install_agent "builder" "Read/Write/Edit/Grep/Glob/Bash"

echo ""
echo "Agent files summary:"
for line in "${AGENT_INSTALL_SUMMARY[@]}"; do
  echo "  - $line"
done
