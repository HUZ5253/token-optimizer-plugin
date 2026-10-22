# token-optimizer-plugin

A Claude Code `PreToolUse` hook that hard-blocks direct `Bash`/`Write`/`Edit`/`NotebookEdit`
calls at the top-level session, forcing mechanical and scoped work to be delegated to cheaper
subagents (`scout`, `runner`, `builder`) instead of burning the top-level session's selected
model on it — even if that session has Opus selected. Delegation-by-convention (a CLAUDE.md
instruction asking nicely) is easy to forget or skip under pressure; this hook makes it
structurally impossible to bypass for the gated tool calls, short of explicitly turning the
hook off.

Delegated work runs on each agent's configured model (`scout`/`runner` on Haiku, `builder` on
Sonnet) rather than whatever model you've selected for the top-level session — see
[Model selection](#model-selection-caveat) below for the one case where that isn't absolute.

`Read` and `Grep` share a single free direct use per session: whichever one you call first
uses it up, and both are gated after that (see the table below). `Glob` is deliberately left out of the gate entirely — it's cheap and read-only, so
this is a decision, not an oversight.

It ships two ways to install it: as a native Claude Code **plugin** (recommended), and as a
**standalone installer** that merges a hook entry into your own `~/.claude/settings.json` for
setups without plugin support.

## Installation

### Option A: Plugin install (recommended)

```
/plugin marketplace add HUZ5253/token-optimizer-plugin
/plugin install token-optimizer-plugin@huz5253-tools
```

The first command points at the GitHub repo; the second's `@...` suffix is the *marketplace*
name (`huz5253-tools`, declared in `.claude-plugin/marketplace.json`), not the repo path.

**To disable or uninstall:**

```
/plugin disable token-optimizer-plugin
/plugin uninstall token-optimizer-plugin
```

These are native CLI operations, not gated tool calls, so the commands themselves always
succeed even while the gate is active — but they differ in when the effect actually lands.
`/plugin uninstall` is **immediate**: the gate stops firing in the current session right away.
`/plugin disable` reports success and shows as disabled in `/plugin list`, but the gate keeps
blocking the very next matched tool call in the already-running session regardless — it only
actually stops gating starting in your **next** session. If you need the gate off right now,
either use `/plugin uninstall`, or run `/plugin disable` and then restart the current session.

The plugin also ships `agents/scout.md`, `agents/runner.md`, and `agents/builder.md` at the
repo root — Claude Code auto-discovers a plugin's `agents/` directory by convention, so these
three subagents are available as soon as the plugin is installed, with no separate step. When
installed this way they're namespaced: the gate's deny messages (and anyone invoking them
manually) use `Agent(token-optimizer-plugin:scout)`, not bare `Agent(scout)`.

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
- Copy the same `scout.md`, `runner.md`, and `builder.md` agent files to `~/.claude/agents/`
  as **user-level** agents (not namespaced, unlike the plugin path). This is done
  **non-destructively**: if a file of the same name already exists there — your own
  `scout`/`runner`/`builder` agent, say — it is left untouched, and the installer prints a
  message saying so for each agent it skipped.

This only takes effect in **new** Claude Code sessions.

**Finding a Python interpreter on Windows.** `install.sh` doesn't just check that `python3`,
`python`, or `py` exist on `PATH` (`command -v`) — it also runs each candidate with a real
`-c` import check, because a non-functional stub (such as the Windows Store's `python3` alias)
passes `command -v` but does nothing useful when actually invoked. If none of the three pass
that check, the installer exits with a clear remediation message: disable the Store alias
under **Settings > Apps > Advanced app settings > App execution aliases**. The hook entry it
writes is exec-form (`"command": "<interpreter>"` plus an `"args"` array holding the absolute
path to the installed gate script), not a shell-form string that depends on expanding `$HOME`
at hook-run time — which also removes the one configuration where a failing interpreter lookup
could have blocked the tool call instead of failing open.

**To uninstall:**

```bash
bash install/uninstall.sh
```

This removes the hook entry from `~/.claude/settings.json` (backing it up first) and optionally
removes the installed gate script and its state directory (it asks for confirmation before
deleting the state directory, since that data is harmless but you should get to decide). It also
removes each of the three agent files from `~/.claude/agents/`, but only if the file is still
byte-identical to what the repo currently ships — i.e. provably untouched since install. Any
agent file that looks customized, or that pre-existed before this plugin ever touched it, is
left alone.

### Installing both ways at once

If you install the plugin **and** run the standalone installer, you'll end up with two
separate `PreToolUse` hook entries firing on every matched tool call — one from the plugin,
one from your `~/.claude/settings.json`. Both must independently exit 0 for the call to
proceed, so the "one free read" budget and all other gating behavior still works correctly;
you'll just see two status/error lines per gated call instead of one. This is noisy, not
broken — an understood tradeoff of running both installs, not a bug.

## What gets blocked vs. allowed

| Call | Result |
|---|---|
| Direct `Bash` / `Write` / `Edit` / `NotebookEdit` at the top level | **Blocked** — always |
| Direct `Read` or `Grep` at the top level, first call of either tool this session | **Allowed** (one free trivial read, shared by `Read` and `Grep` combined) |
| Direct `Read` or `Grep` at the top level, any call after that first one this session (across both tools combined) | **Blocked** |
| Direct `Glob` at the top level | **Allowed** — always, by design (cheap, read-only, never gated) |
| Any call where a subagent (`agent_type`) is already executing | **Allowed** — always exempt |
| (Standalone variant only) `Write`/`Edit`/`NotebookEdit` to your real `~/.claude/settings.json` or `settings.local.json` | **Allowed** — self-repair escape hatch |

When a call is blocked, the hook exits non-zero (2) and feeds a message back to the model
telling it to delegate via `Agent(scout)`, `Agent(runner)`, or `Agent(builder)` instead (or the
`token-optimizer-plugin:`-namespaced equivalents, for the plugin variant).

### Model selection caveat

Claude Code resolves a subagent's model in this order: a per-invocation model Claude explicitly
passes, then the subagent's own `model:` frontmatter (`inherit` defers to the parent), then an
optional `CLAUDE_CODE_SUBAGENT_MODEL` environment variable, then the parent session's model.
`scout`/`runner` (`model: haiku`) and `builder` (`model: sonnet`) sit ahead of the parent
session's model in that order, so delegated work normally runs on the cheaper model regardless
of what the top-level session has selected. It's not an absolute lock, though: the docs don't
rule out Claude passing a different per-invocation model override on a given call, which would
take priority over the frontmatter. Treat the value proposition as "delegated work runs on each
agent's configured model rather than whatever you've selected for the top-level session," not
as an unconditional guarantee.

### `onFailure` is deliberately fail-open

The hook entries in both `hooks/hooks.json` and the one `install.sh` merges into
`settings.json` are left at Claude Code's default (non-blocking) `onFailure` behavior: if the
configured interpreter crashes or can't be found at hook-invocation time, the tool call
proceeds without being gated, rather than getting stuck waiting on a hook that will never
succeed. This is a deliberate design choice, not an oversight. Current Claude Code versions
support `onFailure: "block"`, which would turn this into a true hard-fail gate (the tool call
would be refused if the hook itself failed to run) — that option was deliberately **not**
chosen here, since it would contradict the fail-open philosophy documented throughout this
project.

## Does this actually save money?

It depends on how you're billed, and the honest answer differs between the two cases.

**API / pay-per-token billing: yes, and it's measurable.** Claude Code bills by actual token
consumption on this path, and `/usage` computes per-model dollar estimates from real token
counts. A subagent configured with `model: haiku` genuinely costs less per token than one
running on Opus, so routing mechanical and scoped work to `scout`/`runner`/`builder` instead of
the top-level session's model produces a real, calculable dollar saving here.

**Subscription seat plans (Pro/Max/Team): genuinely unverified, not confirmed either way.** On a
seat plan, usage draws from a seat allowance rather than being metered in dollars inside that
allowance. Subagent requests do count toward the same usage limits as the main conversation
(confirmed), and model-family limits exist (you can hit a separate "Opus limit" from your
general limit), which implies model choice does change which bucket a request draws from — but
there's no documented weighting or ratio between what a Haiku-subagent request costs against
that seat window versus an Opus-subagent request. Don't take this plugin's premise on faith for
a seat plan; verify it in your own account instead:

- **`/tasks`** (Claude Code v2.1.242+) shows which model each running subagent is actually using.
- **`/usage`** has a "Usage by model" breakdown (per-model tokens/dollars) and a "Plan usage
  breakdown" showing the percentage attributed to subagents.
- For Team/Enterprise, **OpenTelemetry events** (`claude_code.cost.usage`,
  `claude_code.token.usage`) carry both a `model` field and a `query_source` field
  (`main`/`subagent`/`auxiliary`) — the most precise way to confirm a given subagent call was
  actually metered against the model it claims to run on.

In short: real and documented for pay-per-token usage, plausible but currently undocumented for
seat plans. Check the commands above against your own account rather than assuming either way.

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
   comparing those resolved paths exactly. Note that only the standalone variant gets this
   carve-out at all — the plugin variant never reads or writes the user's personal
   `~/.claude/settings.json`, so it has no self-repair exemption and denies writes to that path
   like any other `Write`/`Edit` (locked in by test 9, see below).

3. **Race condition under concurrent calls.** Two tool calls racing to read-increment-write the
   same per-session counter file could previously step on each other and under-count. This used
   to be handled with `fcntl.flock`-based exclusive locking, which has been **removed
   entirely** in favor of an atomic check-then-increment built on `os.replace()`: the new count
   is written to a temp file and atomically swapped into place, so there's no read-modify-write
   window left to race, and no locking is needed on either POSIX or Windows. The counter also
   self-heals: if its state file is ever corrupted (e.g. truncated by a crash mid-write), it's
   read back as `0` and immediately rewritten correctly on the next call, rather than
   permanently disabling gating for that session (this was a real bug, now fixed).

The test suite (`python3 tests/test_gate.py`) runs 18 assertions covering both gate variants —
`hooks/gate.py` (plugin) and `install/token_optimizer_gate.py` (standalone) — for every test
where the distinction matters, plus the core allow/deny behavior and regression coverage for
all three issues above. Test 9 specifically locks in that the **plugin** variant has no
self-repair exemption at all (per point 2 above) — only the standalone variant is allowed to
touch its own `settings.json`. Test 10 locks in the counter self-heal from point 3: a counter
file corrupted with non-numeric garbage is read back as `0`, allowed, and rewritten correctly,
then denies the very next call for that session instead of staying permanently stuck open. It
also runs in GitHub Actions on every push and pull request (`.github/workflows/test.yml`).

The standalone variant honors `CLAUDE_CONFIG_DIR` (checked before falling back to
`HOME`/`USERPROFILE` via `os.path.expanduser`) when resolving `~/.claude`, which is also what
makes the test suite reliably cross-platform — tests can point the gate at an isolated temp
directory without touching the real `~/.claude`.

## Known limitations

- **Requires `python3` on `PATH`.** If it's missing, the installer refuses to run with a clear
  error; if it's missing at hook-invocation time, the hook's own fail-open design covers it
  (worst case the gate simply doesn't run — see `onFailure` above).
- **The plugin variant's state directory.** `${CLAUDE_PLUGIN_DATA}` is documented, confirmed
  Claude Code behavior: it resolves to `~/.claude/plugins/data/<plugin-id>/`, is substituted
  correctly inside a hook's `args` array (exactly how `hooks/hooks.json` uses it), and is also
  exported as a real environment variable to the hook process. If it's ever unresolved at
  runtime (still a literal `${...}` placeholder, or unset), the gate falls back to
  `~/.claude/token-optimizer-plugin-state` with `0700` permissions — a private, per-user
  directory rather than shared `/tmp` — so the fallback stays safe even though it isn't
  namespaced per-plugin-install the same way.
