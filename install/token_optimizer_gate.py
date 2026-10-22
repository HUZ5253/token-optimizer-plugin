#!/usr/bin/env python3
"""PreToolUse hard gate (standalone variant): force the top-level session to
delegate T0/T1 work to subagents instead of doing it directly.

Allows (exit 0):
  - Any call where agent_type is set (scout/runner/builder already delegated).
  - Edit/Write resolving to the real ~/.claude/settings.json or
    ~/.claude/settings.local.json (self-repair escape hatch, path-anchored).
  - Exactly one direct Read/Grep per session (a single trivial read).

Denies (exit 2, stderr fed back to the model):
  - Any direct Bash/Write/Edit at the top level.
  - A second (or later) direct Read/Grep in the same session.

Fails open on any error: a bug here must never lock the session out entirely.
"""
import hashlib
import json
import os
import sys
import time

STATE_DIR = os.path.expanduser("~/.claude/.token-optimizer-state")
GLOBAL_SETTINGS = {
    os.path.realpath(os.path.expanduser("~/.claude/settings.json")),
    os.path.realpath(os.path.expanduser("~/.claude/settings.local.json")),
}


def allow():
    sys.exit(0)


def deny(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)


def session_state_path(session_id):
    digest = hashlib.sha256(str(session_id).encode("utf-8", "surrogatepass")).hexdigest()[:24]
    return os.path.join(STATE_DIR, digest + ".count")


def read_count(state_file):
    """Current count, 0 if absent or corrupt, None if unreadable (fail open)."""
    try:
        with open(state_file, "r") as fd:
            raw = fd.read().strip()
    except FileNotFoundError:
        return 0
    except Exception:
        return None
    try:
        return int(raw)
    except ValueError:
        return 0


def write_count(state_file, count):
    """Atomic on POSIX and Windows alike; no lock needed."""
    tmp = "{0}.{1}.tmp".format(state_file, os.getpid())
    try:
        with open(tmp, "w") as fd:
            fd.write(str(count))
            fd.flush()
            os.fsync(fd.fileno())
        os.replace(tmp, state_file)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        allow()
        return

    tool_name = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    agent_type = data.get("agent_type") or ""
    session_id = data.get("session_id") or "unknown"

    if agent_type:
        allow()
        return

    if tool_name in ("Edit", "Write"):
        raw_path = str(tool_input.get("file_path") or "")
        if raw_path:
            try:
                resolved = os.path.realpath(os.path.expanduser(raw_path))
            except Exception:
                resolved = ""
            if resolved in GLOBAL_SETTINGS:
                allow()
                return

    if tool_name in ("Bash", "Write", "Edit"):
        deny(
            "token-optimizer hard rule: direct {0} calls are blocked at the top "
            "level. This is T0/T1 mechanical or scoped work - delegate it via "
            "Agent(scout) for search/reads, Agent(runner) for known commands, "
            "or Agent(builder) for scoped writes, then act on the result. "
            "Direct execution is reserved for T2 judgment calls.".format(tool_name)
        )
        return

    if tool_name in ("Read", "Grep"):
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            cutoff = time.time() - 2 * 86400
            for name in os.listdir(STATE_DIR):
                p = os.path.join(STATE_DIR, name)
                if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                    os.remove(p)
        except Exception:
            allow()
            return

        state_file = session_state_path(session_id)
        count = read_count(state_file)
        if count is None:
            allow()
            return
        if count >= 1:
            deny(
                "token-optimizer hard rule: you have already used your one "
                "direct {0} this session - delegate to Agent(scout) "
                "instead.".format(tool_name)
            )
            return
        write_count(state_file, count + 1)
        allow()
        return

    allow()


if __name__ == "__main__":
    main()
