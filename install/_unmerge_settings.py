#!/usr/bin/env python3
"""Helper used by uninstall.sh: remove the token-optimizer PreToolUse hook
entry from ~/.claude/settings.json.

- Backs up the existing file first (if it exists) to
  ~/.claude/settings.json.bak.<unix-timestamp>.<random-hex>, same scheme as
  install.sh.
- Removes any PreToolUse hook entry whose command (or args) references
  token_optimizer_gate.py.
- Writes the result back, pretty-printed (indent=2), preserving every other
  key untouched.
"""
import json
import os
import secrets
import sys
import time

HOOK_MARKER = "token_optimizer_gate.py"


def references_gate(entry):
    try:
        entry_hooks = entry.get("hooks", [])
    except AttributeError:
        return False
    for h in entry_hooks:
        if not isinstance(h, dict):
            continue
        command = h.get("command", "")
        args = h.get("args", [])
        haystack = command + " " + " ".join(str(a) for a in args)
        if HOOK_MARKER in haystack:
            return True
    return False


def main():
    if len(sys.argv) != 2:
        print("Usage: _unmerge_settings.py <settings_file>", file=sys.stderr)
        sys.exit(1)

    settings_file = sys.argv[1]

    if not os.path.isfile(settings_file):
        print("No settings file found at {0}; nothing to uninstall.".format(settings_file))
        return

    try:
        with open(settings_file, "r") as f:
            raw = f.read().strip()
        data = json.loads(raw) if raw else {}
    except Exception as exc:
        print(
            "Error: could not parse {0} as JSON: {1}".format(settings_file, exc),
            file=sys.stderr,
        )
        sys.exit(1)

    if not isinstance(data, dict):
        print("Error: {0} does not contain a JSON object at the top level.".format(settings_file), file=sys.stderr)
        sys.exit(1)

    hooks = data.get("hooks")
    if not isinstance(hooks, dict) or not isinstance(hooks.get("PreToolUse"), list):
        print("No token-optimizer PreToolUse hook found; nothing to remove.")
        return

    pre_tool_use = hooks["PreToolUse"]
    remaining = [entry for entry in pre_tool_use if not references_gate(entry)]

    if len(remaining) == len(pre_tool_use):
        print("No token-optimizer PreToolUse hook found; nothing to remove.")
        return

    backup_path = "{0}.bak.{1}.{2}".format(
        settings_file, int(time.time()), secrets.token_hex(4)
    )
    with open(settings_file, "r") as src, open(backup_path, "w") as dst:
        dst.write(src.read())

    hooks["PreToolUse"] = remaining
    data["hooks"] = hooks

    with open(settings_file, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    print("Removed token-optimizer PreToolUse hook entry from {0}".format(settings_file))
    print("Backup of your previous settings saved at: {0}".format(backup_path))


if __name__ == "__main__":
    main()
