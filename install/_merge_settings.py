#!/usr/bin/env python3
"""Helper used by install.sh: safely merge the token-optimizer PreToolUse
hook entry into ~/.claude/settings.json.

- Loads existing JSON (or starts from {} if the file is missing/empty).
- Ensures hooks.PreToolUse is a list.
- Idempotent: if an entry already references token_optimizer_gate.py, leaves
  it alone and reports "already installed" instead of duplicating it, and
  does NOT write a backup in that case since nothing is modified. This
  recognizes BOTH the old shell-form entry (a single "command" string like
  'python3 "$HOME/.claude/hooks/token_optimizer_gate.py"') AND the new
  exec-form entry ("command": "<interpreter>", "args": ["<abs-path>/
  token_optimizer_gate.py"]), since the marker check matches the gate
  script's filename anywhere in the command string OR the args array.
- Otherwise, backs up the existing file (only if it existed) to
  ~/.claude/settings.json.bak.<unix-timestamp>.<random-hex>, then appends
  the new hook entry, built in exec form (command + args array) using the
  resolved Python interpreter passed on the command line and an absolute
  path to the installed gate script.
- Writes the merged JSON back, pretty-printed (indent=2), preserving every
  other key untouched.

Also checks (best-effort, advisory only) whether the token-optimizer-plugin
Claude Code plugin's OWN PreToolUse hook entry (installed independently by
Claude Code's native `/plugin install` system, under a path like
~/.claude/plugins/cache/<marketplace>/token-optimizer-plugin/<version>/
hooks/gate.py) is also present in settings.json. If so, prints a non-fatal
warning: having both installed means two independent gates run on every
matched tool call, each enforcing its own separate Read/Grep budget, which
can produce confusing/inconsistent-looking denial messages. This check never
blocks installation and never touches the plugin's own files or
registration - it only reads settings.json.
"""
import json
import os
import secrets
import sys
import time

HOOK_MARKER = "token_optimizer_gate.py"

# Best-effort marker for detecting the native Claude Code plugin install
# (Option A), which registers its own PreToolUse hook pointing at a path
# like .../plugins/cache/<marketplace>/token-optimizer-plugin/<version>/
# hooks/gate.py. This does NOT contain "token_optimizer_gate.py" (that's
# Option B's filename), so HOOK_MARKER alone can't detect it.
PLUGIN_MARKER = "token-optimizer-plugin"


def _entry_haystacks(pre_tool_use):
    """Yield the combined command+args string for every hook in every
    PreToolUse entry, tolerating malformed/unexpected shapes."""
    for entry in pre_tool_use:
        try:
            entry_hooks = entry.get("hooks", [])
        except AttributeError:
            continue
        for h in entry_hooks:
            if not isinstance(h, dict):
                continue
            command = h.get("command", "")
            args = h.get("args", [])
            yield command + " " + " ".join(str(a) for a in args)


def detect_plugin_install(pre_tool_use):
    """Best-effort heuristic: does any PreToolUse entry look like it points
    at the token-optimizer-plugin Claude Code plugin's own hook script
    (e.g. a path containing both a "plugins" cache directory and
    "token-optimizer-plugin", ending in a hook script like gate.py)? Exact
    robustness isn't the goal here - just catching the realistic case of a
    plugin-cache path."""
    for haystack in _entry_haystacks(pre_tool_use):
        if PLUGIN_MARKER in haystack and "plugins" in haystack and "gate.py" in haystack:
            return True
    return False


def warn_if_plugin_also_installed(pre_tool_use, settings_file):
    if not detect_plugin_install(pre_tool_use):
        return
    print(
        "WARNING: the token-optimizer-plugin Claude Code plugin also appears to be "
        "installed (a PreToolUse hook entry referencing its plugins-cache "
        ".../token-optimizer-plugin/.../hooks/gate.py path was found in {0}).".format(
            settings_file
        ),
        file=sys.stderr,
    )
    print(
        "Installing the standalone hook as well means TWO independent gates will run "
        "on every matched tool call, each enforcing its own separate Read/Grep budget. "
        "This can produce confusing/inconsistent-looking denial messages (e.g. a "
        "namespaced 'token-optimizer-plugin:scout' denial from one gate and a plain "
        "'scout' denial from the other on the same session).",
        file=sys.stderr,
    )
    print(
        "If you only want one gate active, remove the other: either the plugin "
        "(`/plugin uninstall token-optimizer-plugin`) or the standalone hook "
        "(`bash install/uninstall.sh`). Installation will proceed.",
        file=sys.stderr,
    )


def build_hook_entry(interpreter, gate_path):
    return {
        "matcher": "Bash|Write|Edit|NotebookEdit|Read|Grep",
        "hooks": [
            {
                "type": "command",
                "command": interpreter,
                "args": [gate_path],
                "timeout": 5,
                "statusMessage": "token-optimizer routing gate",
            }
        ],
    }


def main():
    if len(sys.argv) != 3:
        print("Usage: _merge_settings.py <settings_file> <python_interpreter>", file=sys.stderr)
        sys.exit(1)

    settings_file = sys.argv[1]
    interpreter = sys.argv[2]
    gate_path = os.path.join(
        os.path.dirname(os.path.abspath(settings_file)), "hooks", "token_optimizer_gate.py"
    )
    hook_entry = build_hook_entry(interpreter, gate_path)

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

    # Advisory only: never blocks, never modifies anything, never touches the
    # plugin's own files/registration - just a heads-up before we proceed.
    # This runs before the idempotency loop below has had a chance to
    # validate entry shapes, so swallow any exception here unconditionally:
    # a malformed PreToolUse entry (e.g. {"hooks": null}, or a non-string
    # "command"/"args" value) must never be able to crash the install over
    # an advisory-only check. Worst case: the warning is silently skipped.
    try:
        warn_if_plugin_also_installed(pre_tool_use, settings_file)
    except Exception:
        pass

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
        return

    backup_path = None
    if existed:
        backup_path = "{0}.bak.{1}.{2}".format(
            settings_file, int(time.time()), secrets.token_hex(4)
        )
        with open(settings_file, "r") as src, open(backup_path, "w") as dst:
            dst.write(src.read())

    pre_tool_use.append(hook_entry)
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
