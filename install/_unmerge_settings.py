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
  whether the token-optimizer-plugin Claude Code plugin still appears to be
  installed. Claude Code does NOT merge a plugin's own hooks into
  settings.json - a plugin's hooks live in its own hooks/hooks.json inside
  the plugin cache, never in hooks.PreToolUse - so detection instead looks
  at the two real signals: settings.json's "enabledPlugins" map, and the
  presence of a plugins-cache directory for this plugin. If either signal
  is present, prints a note that the plugin will keep gating tool calls on
  its own, with the one-line command to remove it too. This check never
  blocks anything and never touches the plugin's own files or registration -
  it only reads settings.json and lists the plugins-cache directory.
"""
import json
import os
import secrets
import sys
import time

HOOK_MARKER = "token_optimizer_gate.py"

# Name of the Claude Code plugin as it is registered in settings.json's
# "enabledPlugins" map (keys look like "token-optimizer-plugin@<marketplace>")
# and as the directory name inside the plugins cache - see _merge_settings.py
# for the matching detection used at install time.
PLUGIN_NAME = "token-optimizer-plugin"


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


def _plugin_in_enabled_plugins(data):
    """True if settings.json's enabledPlugins registers this plugin. Keys
    look like "token-optimizer-plugin@<marketplace>". A false value means
    installed-but-disabled, which still warrants the note: a disabled
    plugin is one `/plugin enable` away from double-gating."""
    enabled = data.get("enabledPlugins")
    if not isinstance(enabled, dict):
        return False
    return any(str(key).split("@", 1)[0] == PLUGIN_NAME for key in enabled)


def _plugin_in_cache(claude_dir):
    """True if a plugins-cache directory for this plugin exists, i.e.
    <claude_dir>/plugins/cache/<marketplace>/token-optimizer-plugin/."""
    cache_root = os.path.join(claude_dir, "plugins", "cache")
    try:
        marketplaces = os.listdir(cache_root)
    except Exception:
        return False
    return any(
        os.path.isdir(os.path.join(cache_root, m, PLUGIN_NAME)) for m in marketplaces
    )


def detect_plugin_install(data, settings_file):
    """Best-effort: does the token-optimizer-plugin Claude Code plugin still
    appear to be installed? Claude Code does NOT merge a plugin's hooks into
    settings.json, so scanning the remaining PreToolUse entries for a
    plugin-cache path can never find one - that was the original bug here."""
    if _plugin_in_enabled_plugins(data):
        return True
    return _plugin_in_cache(os.path.dirname(os.path.abspath(settings_file)))


def warn_if_plugin_still_installed(data, settings_file):
    if not detect_plugin_install(data, settings_file):
        return
    print("")
    print(
        "Note: the token-optimizer-plugin Claude Code plugin still appears to be "
        "installed (found via enabledPlugins in {0}, or a plugins-cache directory "
        "beside it). It will keep gating tool calls on its own, with its own "
        "separate Read/Grep budget.".format(settings_file)
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

    if remaining:
        hooks["PreToolUse"] = remaining
    else:
        hooks.pop("PreToolUse", None)

    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)

    with open(settings_file, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    print("Removed token-optimizer PreToolUse hook entry from {0}".format(settings_file))
    print("Backup of your previous settings saved at: {0}".format(backup_path))

    # Advisory only - see the matching note in _merge_settings.py. A
    # malformed "enabledPlugins" shape or an unreadable plugins-cache
    # directory must never be able to raise here; the removal above has
    # already succeeded and been written, so just skip the note.
    try:
        warn_if_plugin_still_installed(data, settings_file)
    except Exception:
        pass


if __name__ == "__main__":
    main()
