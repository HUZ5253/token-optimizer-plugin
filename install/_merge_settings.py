#!/usr/bin/env python3
"""Helper used by install.sh: safely merge the token-optimizer PreToolUse
hook entry into ~/.claude/settings.json.

- Loads existing JSON (or starts from {} if the file is missing/empty).
- Backs up the existing file first (only if it existed) to
  ~/.claude/settings.json.bak.<unix-timestamp>.<random-hex>.
- Ensures hooks.PreToolUse is a list.
- Idempotent: if an entry already references token_optimizer_gate.py, leaves
  it alone and reports "already installed" instead of duplicating it.
- Otherwise appends the new hook entry.
- Writes the merged JSON back, pretty-printed (indent=2), preserving every
  other key untouched.
"""
import json
import os
import secrets
import sys
import time

HOOK_MARKER = "token_optimizer_gate.py"
HOOK_ENTRY = {
    "matcher": "Bash|Write|Edit|Read|Grep",
    "hooks": [
        {
            "type": "command",
            "command": 'python3 "$HOME/.claude/hooks/token_optimizer_gate.py"',
            "timeout": 5,
            "statusMessage": "token-optimizer routing gate",
        }
    ],
}


def main():
    if len(sys.argv) != 2:
        print("Usage: _merge_settings.py <settings_file>", file=sys.stderr)
        sys.exit(1)

    settings_file = sys.argv[1]

    existed = os.path.isfile(settings_file)
    data = {}
    if existed:
        try:
            with open(settings_file, "r") as f:
                raw = f.read().strip()
            if raw:
                data = json.loads(raw)
        except Exception as exc:
            print(
                "Error: could not parse existing {0} as JSON: {1}".format(
                    settings_file, exc
                ),
                file=sys.stderr,
            )
            sys.exit(1)

    if not isinstance(data, dict):
        print(
            "Error: {0} does not contain a JSON object at the top level.".format(
                settings_file
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    backup_path = None
    if existed:
        backup_path = "{0}.bak.{1}.{2}".format(
            settings_file, int(time.time()), secrets.token_hex(4)
        )
        with open(settings_file, "r") as src, open(backup_path, "w") as dst:
            dst.write(src.read())

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        print("Error: existing 'hooks' key is not an object.", file=sys.stderr)
        sys.exit(1)

    pre_tool_use = hooks.get("PreToolUse")
    if pre_tool_use is None:
        pre_tool_use = []
    if not isinstance(pre_tool_use, list):
        print("Error: existing 'hooks.PreToolUse' is not a list.", file=sys.stderr)
        sys.exit(1)

    already_installed = False
    for entry in pre_tool_use:
        try:
            entry_hooks = entry.get("hooks", [])
        except AttributeError:
            continue
        for h in entry_hooks:
            command = h.get("command", "") if isinstance(h, dict) else ""
            args = h.get("args", []) if isinstance(h, dict) else []
            haystack = command + " " + " ".join(str(a) for a in args)
            if HOOK_MARKER in haystack:
                already_installed = True
                break
        if already_installed:
            break

    if already_installed:
        print("token-optimizer: already installed (PreToolUse hook entry found) - leaving settings.json unchanged.")
        if backup_path:
            print("A backup was still made at: {0}".format(backup_path))
        return

    pre_tool_use.append(HOOK_ENTRY)
    hooks["PreToolUse"] = pre_tool_use
    data["hooks"] = hooks

    with open(settings_file, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    print("token-optimizer: installed PreToolUse hook into {0}".format(settings_file))
    if backup_path:
        print("Backup of your previous settings saved at: {0}".format(backup_path))
    else:
        print("(No previous settings.json existed, so no backup was needed.)")
    print("")
    print("This only takes effect in NEW Claude Code sessions - restart any")
    print("running sessions for the hook to apply.")


if __name__ == "__main__":
    main()
