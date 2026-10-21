#!/usr/bin/env python3
"""PreToolUse hard gate (plugin variant): force the top-level session to
delegate T0/T1 work to subagents instead of doing it directly.

This hook ships inside the token-optimizer-plugin Claude Code plugin. It
never writes to or reads from the user's personal ~/.claude/settings.json -
all of its own state lives under the plugin's own data directory. To
disable or remove this behavior, use `/plugin disable token-optimizer-plugin`
or `/plugin uninstall token-optimizer-plugin` - those are native CLI
operations, not Bash/Write/Edit/Read/Grep tool calls, so they always work
even while this gate is active.

Allows (exit 0):
  - Any call where agent_type is set (a named subagent is already executing
    delegated work).
  - Exactly one direct Read/Grep per session (a single trivial read).

Denies (exit 2, stderr fed back to the model):
  - Any direct Bash/Write/Edit at the top level.
  - A second (or later) direct Read/Grep in the same session.

Fails open on any error: a bug here must never lock the session out.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import time

try:
    import fcntl
except ImportError:
    fcntl = None


def resolve_state_dir(cli_arg):
    for candidate in (cli_arg, os.environ.get("CLAUDE_PLUGIN_DATA")):
        if candidate and not candidate.startswith("${"):
            return candidate
    return os.path.join(tempfile.gettempdir(), "token-optimizer-plugin-state")


def allow():
    sys.exit(0)


def deny(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)


def session_state_path(state_dir, session_id):
    digest = hashlib.sha256(str(session_id).encode("utf-8", "surrogatepass")).hexdigest()[:24]
    return os.path.join(state_dir, digest + ".count")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default=None)
    args, _ = parser.parse_known_args()
    state_dir = resolve_state_dir(args.state_dir)

    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        allow()
        return

    tool_name = data.get("tool_name") or ""
    agent_type = data.get("agent_type") or ""
    session_id = data.get("session_id") or "unknown"

    if agent_type:
        allow()
        return

    if tool_name in ("Bash", "Write", "Edit"):
        deny(
            "token-optimizer hard rule: direct {0} calls are blocked at the top "
            "level. Delegate via Agent(scout) for search/reads, Agent(runner) "
            "for known commands, or Agent(builder) for scoped writes. To turn "
            "this off: /plugin disable token-optimizer-plugin.".format(tool_name)
        )
        return

    if tool_name in ("Read", "Grep"):
        try:
            os.makedirs(state_dir, exist_ok=True)
            cutoff = time.time() - 2 * 86400
            for name in os.listdir(state_dir):
                p = os.path.join(state_dir, name)
                if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                    os.remove(p)
        except Exception:
            allow()
            return

        state_file = session_state_path(state_dir, session_id)
        count = 1
        try:
            fd = open(state_file, "a+")
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                fd.seek(0)
                raw = fd.read().strip()
                count = int(raw) if raw else 0
                count += 1
                fd.seek(0)
                fd.truncate()
                fd.write(str(count))
                fd.flush()
            finally:
                if fcntl is not None:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except Exception:
                        pass
                fd.close()
        except Exception:
            pass

        if count <= 1:
            allow()
            return

        deny(
            "token-optimizer hard rule: direct {0} #{1} this session - delegate "
            "to Agent(scout) instead of continuing to read directly.".format(tool_name, count)
        )
        return

    allow()


if __name__ == "__main__":
    main()
