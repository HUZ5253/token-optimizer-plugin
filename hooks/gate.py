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
  - Up to FREE_READ_BUDGET direct Read/Grep calls per session (a small
    batch of trivial reads).

Denies (exit 2, stderr fed back to the model):
  - Any direct Bash/Write/Edit at the top level.
  - A Read/Grep in the same session beyond the free budget.

Fails open on any error: a bug here must never lock the session out.
"""
import argparse
import hashlib
import json
import os
import sys
import time

# Free direct Read/Grep calls allowed per session before delegation is
# forced. This is a deliberate, tunable judgment call (not a hardcoded
# magic number): live-measured subagent delegation overhead is large
# enough (7k-16k tokens per trivial single-shot call) that forcing
# delegation after just one direct read is economically counterproductive
# for the common case of checking a couple of related files. 3 keeps real
# exploration batches (4+) forced through scout, where delegation's
# overhead actually amortizes.
FREE_READ_BUDGET = 3


def resolve_state_dir(cli_arg):
    for candidate in (cli_arg, os.environ.get("CLAUDE_PLUGIN_DATA")):
        if candidate and not candidate.startswith("${"):
            return candidate
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, ".claude", "token-optimizer-plugin-state")


def allow():
    sys.exit(0)


def deny(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)


def session_state_path(state_dir, session_id):
    digest = hashlib.sha256(str(session_id).encode("utf-8", "surrogatepass")).hexdigest()[:24]
    return os.path.join(state_dir, digest + ".count")


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

    if tool_name in ("Bash", "Write", "Edit", "NotebookEdit"):
        deny(
            "token-optimizer hard rule: direct {0} calls are blocked at the top "
            "level. Delegate via Agent(token-optimizer-plugin:scout) for "
            "search/reads, Agent(token-optimizer-plugin:runner) for known "
            "commands, or Agent(token-optimizer-plugin:builder) for scoped "
            "writes. To turn this off: /plugin disable token-optimizer-plugin.".format(tool_name)
        )
        return

    if tool_name in ("Read", "Grep"):
        try:
            os.makedirs(state_dir, mode=0o700, exist_ok=True)
            cutoff = time.time() - 2 * 86400
            for name in os.listdir(state_dir):
                p = os.path.join(state_dir, name)
                if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                    os.remove(p)
        except Exception:
            allow()
            return

        state_file = session_state_path(state_dir, session_id)
        count = read_count(state_file)
        if count is None:
            allow()
            return
        if count >= FREE_READ_BUDGET:
            deny(
                "token-optimizer hard rule: you have already used your {1} "
                "free direct {0} calls this session - delegate to "
                "Agent(token-optimizer-plugin:scout) instead.".format(tool_name, FREE_READ_BUDGET)
            )
            return
        write_count(state_file, count + 1)
        allow()
        return

    allow()


if __name__ == "__main__":
    main()
