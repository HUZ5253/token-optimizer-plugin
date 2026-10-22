# token-optimizer-plugin

A Claude Code `PreToolUse` hook that hard-blocks direct `Bash`/`Write`/`Edit`/`NotebookEdit`
calls at the top-level session, forcing mechanical and scoped work to be delegated to cheaper
subagents (`scout`, `runner`, `builder`) instead of burning the top-level session's selected
model on it — even if that session has Opus selected. Delegation-by-convention (a CLAUDE.md
instruction asking nicely) is easy to forget or skip under pressure; this hook makes it
structurally impossible to bypass for the gated tool calls, short of explicitly turning the
hook off.

**What this is actually for.** The proven, unconditional benefit is keeping the top-level
session's context clean and enforcing a deliberate boundary around side-effecting actions
(`Bash`/`Write`/`Edit`/`NotebookEdit`) — that holds regardless of token economics. Token/cost
savings are real but workload-dependent: they show up for bulky delegated work (reading or
searching many files, a substantial scoped implementation) and can be net-negative for a
trivial single-shot call, because every delegation carries several thousand tokens of fixed
subagent overhead. See [Why 3, and why Bash/Write/Edit stay fully gated](#why-3-and-why-bashwriteedit-stay-fully-gated)
below for the measured data behind that distinction, and
[Does this actually save money?](#does-this-actually-save-money) for the billing caveats.

Delegated work runs on each agent's configured model (`scout`/`runner` on Haiku, `builder` on
Sonnet) rather than whatever model you've selected for the top-level session — see
[Model selection](#model-selection-caveat) below for the one case where that isn't absolute.

`Read` and `Grep` share a combined free-direct-use budget per session (`FREE_READ_BUDGET`,
currently 3): whichever one you call first starts drawing it down, and both are gated once it's
exhausted (see the table below). `Glob` is deliberately left out of the gate entirely — it's cheap and read-only, so
this is a decision, not an oversight (though see the sharp-edges note below about `Glob "*"`
recursing further than expected on some setups).

It ships two ways to install it: as a native Claude Code **plugin** (recommended), and as a
**standalone installer** that merges a hook entry into your own `~/.claude/settings.json` for
setups without plugin support.

## Installation

### Option A: Plugin install (recommended)

> **Windows warning: `python3` may be a non-functional stub, and a python.org install
> alone will not fix it.** On a default Windows machine, `python3` on `PATH` is
> commonly the Microsoft Store's alias stub — it exists and passes every presence
> check (`command -v python3`, `where python3`, etc.), but exits with code 9009 when
> actually spawned. The hooks this plugin installs run via `hooks/hooks.json`'s
> `"command": "python3"`, so when the stub is what's on `PATH`, the gate never runs:
> it fails open (nothing gets blocked), and Claude Code shows a hook error on every
> matched tool call. **Installing Python from python.org is not sufficient by
> itself** — that installer creates `python.exe`, never `python3.exe`, so it doesn't
> change what `python3` resolves to. **How to check:** run `python3 -c "import sys"` —
> a presence check like `command -v`/`where` will *not* catch this; only actually
> trying to run the interpreter does. **How to fix it:** either (a) disable the Store
> alias under **Settings > Apps > Advanced app settings > App execution aliases**, or
> (b) copy `python.exe` to `python3.exe` inside your Python installation directory,
> making sure that directory comes *before* the WindowsApps alias directory on
> `PATH`. Option B below (the standalone installer) detects and reports this
> automatically — it runs a real functional probe before writing its hook entry.
> Option A has no install step at all (it's a slash command, not a script), so
> there's nothing in the plugin path that can catch this for you.

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
proceed, so the shared Read/Grep free budget and all other gating behavior still works
correctly; you'll just see two status/error lines per gated call instead of one. This is
noisy, not broken — an understood tradeoff of running both installs, not a bug.

## What gets blocked vs. allowed

| Call | Result |
|---|---|
| Direct `Bash` / `Write` / `Edit` / `NotebookEdit` at the top level | **Blocked** — always |
| Direct `Read` or `Grep` at the top level, within the first `FREE_READ_BUDGET` (3) calls to either tool this session | **Allowed** (a small free budget of trivial reads, shared by `Read` and `Grep` combined) |
| Direct `Read` or `Grep` at the top level, any call once that combined budget of 3 is exhausted this session | **Blocked** |
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
take priority over the frontmatter. Live testing (see
[Does this actually save money?](#does-this-actually-save-money)) confirmed the frontmatter
model winning on all 7 observed calls, but 7 calls can't rule out a different override on some
future one. Treat the value proposition as "delegated work runs on each agent's configured model
rather than whatever you've selected for the top-level session," not as an unconditional
guarantee.

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

## Why 3, and why Bash/Write/Edit stay fully gated

Two more rounds of live smoke-testing (through Claude Code's real `/plugin` system) measured
actual subagent delegation overhead for trivial, single-shot tasks — the task notifications
report tokens, tool uses, and duration for every delegated call:

| Call | Task | Tokens | Tool uses | Duration |
|---|---|---|---|---|
| `runner` | "which file changed most recently" | 7,114 | 2 | 9.1s |
| `builder` | write an 11-byte file | 8,931 | 3 | 9.6s |
| `scout` | grep top-level files for "TODO" | 15,861 | 5 | 28.3s |

Every delegation carries several thousand tokens of fixed overhead (the subagent's own system
prompt, tool definitions, reasoning) versus a few hundred tokens for the same trivial work done
directly. Even granting Haiku's full ~1/20th-of-Opus discount, 15,861 Haiku tokens is roughly
800 Opus-equivalent tokens against maybe 300 for a direct `Grep`. **For small, single-shot
tasks, the gate plausibly costs more than it saves.**

**The design decision made in response:** `FREE_READ_BUDGET` (defined in both
`hooks/gate.py` and `install/token_optimizer_gate.py`) was raised from 1 to 3. Reasoning:
`Read`/`Grep` are side-effect-free, so the only justification for gating them at all is token
economics — and the data above shows that economics is negative at a budget of 1 for the
common case of checking 2-3 related files directly. Raising it to 3 keeps genuine exploration
batches (4+ reads, where `scout`'s overhead actually amortizes against the work) forced
through delegation, while letting realistic small lookups stay direct and cheap.

`FREE_READ_BUDGET` is a tunable constant, and 3 is a judgment call based on this data, not a
precisely-derived optimum. Someone with a different mix of usage — mostly large exploration
batches, or mostly one-off single-file checks — may reasonably want a different number; change
the constant in both gate scripts if so.

**`Bash`/`Write`/`Edit`/`NotebookEdit` remain fully gated (no free allowance), deliberately
unchanged.** Their justification was never purely economic. They have side effects, and the
plugin's demonstrated, provable benefit — keeping the top-level session's context clean,
enforcing a deliberate boundary around actions with consequences — holds regardless of whether
any single instance nets out token-positive. Raising their budget the way `Read`/`Grep`'s was
raised would trade away that guarantee for the same economics that only justify it for
read-only calls.

### Two sharp edges observed during live testing

Neither of these is a plugin bug, but both affect real usage and are worth knowing about:

- **`Glob "*"` can recurse into subdirectories** rather than matching only the top level on
  some setups, which cost `:scout` real tokens in testing — it had to fall back to a
  `Grep`-based approach instead. `Glob` is deliberately ungated (see above), and `scout` has
  `Glob` in its tool list, so if `Glob` seems to be matching far more than expected, prefer an
  explicit `Grep` with a scoped glob pattern.
- **A top-level session's own tool-routing preferences can leak into a subagent's behavior.**
  For example, an auto-mode instruction to "prefer Bash for reads" was observed influencing a
  subagent's choices even though the subagent has its own scoped tool list. Harmless, but worth
  being aware of — a subagent's declared tools aren't a complete guarantee of its behavior,
  only of what it's permitted to call.

## Does this actually save money?

It depends on how you're billed, and the honest answer differs between the two cases — but one
piece of the premise is no longer in question either way you're billed.

**Confirmed: the cheaper model actually does the delegated work.** Claude Code records a
`resolvedModel` field per subagent invocation in the session transcript JSONL. Across seven real
subagent calls made from Opus-running sessions, every `:scout`/`:runner` call (frontmatter
`model: haiku`) resolved to `claude-haiku-5-5`, and the `:builder` call (frontmatter
`model: sonnet`) resolved to `claude-sonnet-5-5` — seven for seven. That's direct, empirical
confirmation that the frontmatter `model:` field determines which model actually serves a
delegated request, not just a nominal default something else can quietly override.

**API / pay-per-token billing: yes, and it's measurable.** Claude Code bills by actual token
consumption on this path, and `/usage` computes per-model dollar estimates from real token
counts. A subagent configured with `model: haiku` genuinely costs less per token than one
running on Opus, so routing mechanical and scoped work to `scout`/`runner`/`builder` instead of
the top-level session's model produces a real, calculable dollar saving here.

**Subscription seat plans (Pro/Max/Team): the model doing the work is confirmed; whether that
lowers your seat usage is inference, not a direct measurement.** On a seat plan, usage draws from
a seat allowance rather than being metered in dollars. Subagent requests do count toward the same
usage limits as the main conversation (confirmed), and model-family limits exist (you can hit a
separate "Opus limit" from your general limit), which implies model choice does change which
bucket a request draws from. What isn't measured is the next step — that Haiku tokens draw
proportionally less from that allowance than Opus tokens would for the same work. Plan usage is
reported as a single "all models" aggregate figure with no per-model breakdown, so there's no way
to directly observe that weighting; "lower consumption" here is a reasonable inference (metered
allowances are generally weighted by model cost), not something confirmed the way the model
routing above now is. Don't take the consumption half of this on faith; verify what you can in
your own account instead:

- **`/tasks`** (Claude Code v2.1.242+) shows which model each running subagent is actually using.
- **`/usage`** has a "Usage by model" breakdown (per-model tokens/dollars) and a "Plan usage
  breakdown" showing the percentage attributed to subagents.
- For Team/Enterprise, **OpenTelemetry events** (`claude_code.cost.usage`,
  `claude_code.token.usage`) carry both a `model` field and a `query_source` field
  (`main`/`subagent`/`auxiliary`) — the most precise way to confirm a given subagent call was
  actually metered against the model it claims to run on.

In short: confirmed, in both cases, that the cheaper model is the one doing the delegated work;
confirmed and measurable that this saves real dollars on pay-per-token billing; only the
translation to lower seat-plan consumption remains an inference rather than a direct measurement.
Check the commands above against your own account rather than assuming either way.

Even setting the subscription-billing question aside entirely, the overhead data in
[Why 3, and why Bash/Write/Edit stay fully gated](#why-3-and-why-bashwriteedit-stay-fully-gated)
above narrows the claim further: savings, where they exist at all, are concentrated in bulkier
delegated tasks (reading/searching many files, a substantial scoped implementation), not small
single-shot ones — which is exactly why the `Read`/`Grep` free budget was raised from 1 to 3
rather than left forcing every trivial lookup through delegation.

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

   **What this does and doesn't guarantee.** The atomic `os.replace()` write guarantees the
   counter file is *never* corrupted or torn, even if a process is killed mid-write — the live
   file is only ever swapped in after a full write completes, so a reader always sees either
   the old value or the new one, never a partial one. It does *not* guarantee an exactly
   precise count under true simultaneous concurrency: a narrow race could in theory let
   slightly more than the configured budget through under heavy simultaneous load. This is an
   accepted, deliberate tradeoff — real file locking (`fcntl.flock`) was the mechanism that was
   broken on Windows in the first place (this finding), so correctness here was redefined as
   "never corrupts, always self-heals" rather than "perfectly exact under all concurrency,"
   which is sufficient for this plugin's purpose (a soft budget nudge toward delegation, not a
   security boundary).

The test suite (`python3 tests/test_gate.py`) runs 20 assertions covering both gate variants —
`hooks/gate.py` (plugin) and `install/token_optimizer_gate.py` (standalone) — for every test
where the distinction matters, plus the core allow/deny behavior and regression coverage for
all three issues above. Test 9 specifically locks in that the **plugin** variant has no
self-repair exemption at all (per point 2 above) — only the standalone variant is allowed to
touch its own `settings.json`. Test 10 locks in the counter self-heal from point 3: a counter
file corrupted with non-numeric garbage is read back as `0`, allowed, and rewritten correctly,
then denies the call once the (now 3-call) free budget for that session is exhausted, instead
of staying permanently stuck open. Test 11 is a dedicated regression test for the other half of
point 3 — the "never produces a torn/unparseable file under concurrent writes" guarantee itself
— firing 10 concurrent gate invocations against one shared session (for each of the two
variants) and asserting the state file is still parseable as an integer afterward; it
deliberately does not assert the final count is exact, since that's the tradeoff documented
above. It also runs in GitHub Actions on every push and pull request (`.github/workflows/test.yml`).

The standalone variant honors `CLAUDE_CONFIG_DIR` (checked before falling back to
`HOME`/`USERPROFILE` via `os.path.expanduser`) when resolving `~/.claude`, which is also what
makes the test suite reliably cross-platform — tests can point the gate at an isolated temp
directory without touching the real `~/.claude`.

## Known limitations

- **The plugin path (Option A) has no way to catch a non-functional `python3`.**
  `hooks/hooks.json` declares `"command": "python3"` unconditionally, with no install
  step and no probe in front of it. On a default Windows machine that name commonly
  resolves to the Microsoft Store's alias stub: present on `PATH`, passing every
  existence check, but exiting 9009 when actually spawned — so the hook fails open and
  the gate silently never runs (see the Windows warning under
  [Option A](#option-a-plugin-install-recommended) above). This is a *non-functional*
  interpreter reachable via a path with no installer in front of it to catch it, not a
  *missing* one an installer would refuse to proceed without — only Option B's
  standalone installer runs that functional probe, because only it has an install step
  to run one in. It also can't be fixed in code: `hooks.json`'s `command` field is a
  single static string with no OS-conditional branching, and there's no one command
  name that's correct on every platform — `python3` is right on macOS/Linux and wrong
  on default Windows, `python` is usually the reverse, `py` is Windows-only, and a
  shell form would need Git Bash on Windows, where a *failing* shell-form hook can
  actually **block** a call rather than fail open, a worse failure mode than the
  current one. Documenting this as a limitation, rather than trading one
  undiagnosable failure mode for a different one, is the deliberate choice here.
- **The plugin variant's state directory.** `${CLAUDE_PLUGIN_DATA}` is documented, confirmed
  Claude Code behavior: it resolves to `~/.claude/plugins/data/<plugin-id>/`, is substituted
  correctly inside a hook's `args` array (exactly how `hooks/hooks.json` uses it), and is also
  exported as a real environment variable to the hook process. If it's ever unresolved at
  runtime (still a literal `${...}` placeholder, or unset), the gate falls back to
  `~/.claude/token-optimizer-plugin-state` with `0700` permissions — a private, per-user
  directory rather than shared `/tmp` — so the fallback stays safe even though it isn't
  namespaced per-plugin-install the same way.
