# token-optimizer-plugin

A Claude Code `PreToolUse` hook that hard-blocks direct `Bash`/`Write`/`Edit`/`Read`/`Grep`
calls at the top-level session, forcing mechanical and scoped work to be delegated to cheaper
subagents (`scout`, `runner`, `builder`) instead of burning the top-level session's selected
model on it — even if that session has Opus selected. Delegation-by-convention (a CLAUDE.md
instruction asking nicely) is easy to forget or skip under pressure; this hook makes it
structurally impossible to bypass for the gated tool calls, short of explicitly turning the
hook off.

It ships two ways to install it: as a native Claude Code **plugin** (recommended), and as a
**standalone installer** that merges a hook entry into your own `~/.claude/settings.json` for
setups without plugin support.

## Installation

### Option A: Plugin install (recommended)

```
/plugin marketplace add HUZ5253/token-optimizer-plugin
/plugin install token-optimizer-plugin@HUZ5253/token-optimizer-plugin
```

**To disable or uninstall:**

```
/plugin disable token-optimizer-plugin
/plugin uninstall token-optimizer-plugin
```

These are native CLI operations, not gated tool calls, so they always work even while the gate
is active.

### Option B: Standalone install (no plugin support needed)

Clone this repo, then run:

```bash
bash install/install.sh
```

This will:
- Create `~/.claude` and `~/.claude/hooks` if they don't exist.
- Copy `install/token_optimizer_gate.py` to `~/.claude/hooks/token_optimizer_gate.py`.
- Safely merge a `PreToolUse` hook entry into `~/.claude/settings.json` (backing up the
  existing file first, and leaving every other key untouched). Running it more than once is
  safe — it detects an existing entry and won't duplicate it.

This only takes effect in **new** Claude Code sessions.

**To uninstall:**

```bash
bash install/uninstall.sh
```

This removes the hook entry from `~/.claude/settings.json` (backing it up first) and optionally
removes the installed gate script and its state directory (it asks for confirmation before
deleting the state directory, since that data is harmless but you should get to decide).

## What gets blocked vs. allowed

| Call | Result |
|---|---|
| Direct `Bash` / `Write` / `Edit` at the top level | **Blocked** — always |
| Direct `Read` / `Grep` at the top level, 1st time this session | **Allowed** (one free trivial read) |
| Direct `Read` / `Grep` at the top level, 2nd+ time this session | **Blocked** |
| Any call where a subagent (`agent_type`) is already executing | **Allowed** — always exempt |
| (Standalone variant only) `Write`/`Edit` to your real `~/.claude/settings.json` or `settings.local.json` | **Allowed** — self-repair escape hatch |

When a call is blocked, the hook exits non-zero and feeds a message back to the model telling
it to delegate via `Agent(scout)`, `Agent(runner)`, or `Agent(builder)` instead.

## Security notes

This logic went through several rounds of adversarial testing before being published. Three
real issues were found and fixed:

1. **Path traversal via `session_id`.** An attacker-influenced or malformed `session_id`
   (e.g. `../../evil` or an absolute path) could previously have caused state files to be
   written outside the intended state directory. **Fixed** by hashing `session_id` with
   SHA-256 and using the hex digest as the filename, so it can never contain path separators.

2. **Overly-broad "self-repair" exemption.** The standalone variant needs to be able to edit
   its own `~/.claude/settings.json` to fix itself (e.g. during an uninstall-and-reinstall
   flow). An earlier version matched on filename suffix alone (anything ending in
   `settings.json`), which would also exempt an attacker-chosen path like
   `/tmp/fake_settings.json`. **Fixed** by resolving the real path (`os.path.realpath`) of both
   the incoming `file_path` and the real `~/.claude/settings.json` / `settings.local.json`, and
   comparing those resolved paths exactly.

3. **Race condition under concurrent calls.** Two tool calls racing to read-increment-write the
   same per-session counter file could step on each other and under-count. **Fixed** with
   `fcntl.flock`-based exclusive locking around the read-modify-write.

The test suite (`python3 tests/test_gate.py`) codifies regression coverage for all three of
these, plus the core allow/deny behavior, as automated, repeatable, CI-friendly checks. It also
runs in GitHub Actions on every push and pull request (`.github/workflows/test.yml`).

## Known limitations

- **Requires `python3` on `PATH`.** If it's missing, the installer refuses to run with a clear
  error; if it's missing at hook-invocation time, the hook's own fail-open design covers it
  (worst case the gate simply doesn't run).
- **File locking is POSIX-only (`fcntl`).** On native Windows, `fcntl` isn't available, so the
  race-condition protection silently degrades (no crash — the lock is just skipped). This is a
  known, documented gap, not a regression; WSL and macOS/Linux are unaffected.
- **The plugin variant's use of `${CLAUDE_PLUGIN_DATA}` for state has NOT yet been empirically
  verified against a real `/plugin install` flow.** The logic assumes Claude Code populates
  this environment variable with a writable, per-plugin data directory at hook invocation time.
  **Please smoke-test this before relying on it in production** — this is a TODO / please-verify
  item, not a confirmed-working claim. If `${CLAUDE_PLUGIN_DATA}` is unset or still a literal
  `${...}` placeholder at runtime, the gate falls back to a directory under the system temp dir,
  which is functional but not ideal (it won't be namespaced per-plugin-install the same way).
