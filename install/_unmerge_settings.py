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
- After removing the standalone entry, checks (best-effort, advisory only)
  whether the token-optimizer-plugin Claude Code plugin's OWN PreToolUse
  hook entry (installed independently by Claude Code's native
  `/plugin install` system) is still present. If so, prints a note that the
  plugin will keep gating tool calls on its own, with the one-line command
  to remove it too. This check never blocks anything and never touches the
  plugin's own files or registration - it only reads settings.json.
"""
import json
import os
import secrets
import sys
import time

HOOK_MARKER = "token_optimizer_gate.py"

# Best-effort marker for detecting the native Claude Code plugin install
# (Option A) - see _merge_settings.py for the matching heuristic used at
# install time.
PLUGIN_MARKER = "token-optimizer-plugin"


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


def references_plugin_install(entry):
    """Best-effort heuristic: does this PreToolUse entry look like it points
    at the token-optimizer-plugin Claude Code plugin's own hook script
    (e.g. a path containing both a "plugins" cache directory and
    "token-optimizer-plugin", ending in a hook script like gate.py)?"""
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
        if PLUGIN_MARKER in haystack and "plugins" in haystack and "gate.py" in haystack:
            return True
    return False


def warn_if_plugin_still_installed(remaining_entries):
    if not any(references_plugin_install(e) for e in remaining_entries):
        return
    print("")
    print(
        "Note: the token-optimizer-plugin Claude Code plugin still appears to be "
        "installed (its own PreToolUse hook entry is still present in settings.json). "
        "It will keep gating tool calls on its own, with its own separate Read/Grep "
        "budget."
    )
    print("To remove it too: /plugin uninstall token-optimizer-plugin")


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

    # Advisory only - see the matching note in _merge_settings.py. A
    # malformed remaining entry (e.g. {"hooks": null}, or a non-string
    # "command"/"args" value) must never be able to raise here; the removal
    # above has already succeeded and been written, so just skip the note.
    try:
        warn_if_plugin_still_installed(remaining)
    except Exception:
        pass


if __name__ == "__main__":
    main()
