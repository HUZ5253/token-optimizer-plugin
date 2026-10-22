---
name: token-optimizer
description: Route each task to the cheapest capable model and cut token waste. Use at the start of any project or multi-step task, when delegating to subagents, when planning work, or when the user mentions tokens, usage limits, cost, Haiku/Sonnet/Opus, or routing.
---

# Token Optimizer

Goal: same quality, fewer tokens. Never trade quality for savings.

## Install

Nothing to install. Copy this whole folder to `~/.claude/skills/token-optimizer` (every
project) or `<project>/.claude/skills/token-optimizer` (one project), and it's active. No
hooks, no settings.json edits, no restart. If you already have the `token-optimizer-plugin`
installed, this directory ships inside it too.

Optional, for named delegation (`Agent(scout)` instead of a raw model override): copy the
three files in `agents/` to `~/.claude/agents/` or `<project>/.claude/agents/`. Skip this if
you'd rather just pass `model:` on a plain `Agent` call — see Step 2 below.

## Step 0 — mandatory, not advisory

Before doing more than one direct Read/Grep/Bash/Write yourself, classify the task below and
delegate if it's T0/T1. Applies every time: direct chat, a relay inside a Project/Cowork
session, a resumed thread — and to non-code work (documents, branding, ops, content) exactly
like code. Self-check each turn: "Am I about to do T0/T1 work myself instead of delegating?"
If yes, delegate first. Exception: a single trivial read.

## Step 1 — classify

| Tier | Model | Task types |
|---|---|---|
| T0 Mechanical | Haiku | file search, grep, reading logs, listing, rename, formatting, summarizing a file/doc, doc lookup, running a known command and reporting output |
| T1 Standard | Sonnet | writing/editing code to a clear spec, tests, scoped refactors, content drafting, copywriting, data cleaning, UI build from an approved design |
| T2 Hard | Opus (do it yourself) | planning, architecture, ambiguous requirements, tricky debugging, security review, final review, irreversible or financial decisions |

Escalate one tier if: the spec is ambiguous, the task touches auth/payments/data loss, or a
lower tier already failed once. Never de-escalate security, payments, or irreversible actions.

## Step 2 — delegate

Call the `Agent` tool. Two ways to pick the model — use whichever you set up:

- **Named agents installed** (see Install above): `Agent(scout)` / `Agent(runner)` for T0,
  `Agent(builder)` for T1.
- **No named agents**: pass `model` directly — `Agent({ subagent_type: "general-purpose",
  model: "haiku", prompt: "..." })` for T0, `model: "sonnet"` for T1. Works with zero setup.

Either way, every delegation gets: exact goal, file paths, constraints, expected output
format, "report in under N lines." Do T2 yourself — plan, then let T0/T1 steps run cheap.

## Step 3 — delegation rules

- Haiku never spawns further agents; a subagent that hits something hard returns to you, it
  doesn't escalate itself.
- Don't set `CLAUDE_CODE_SUBAGENT_MODEL` globally — it overrides every agent including ones
  that must run on Opus. Set `model` per call or per agent file instead.
- Keep Haiku prompts under ~100K tokens (Haiku charges ~5x more per token above that).
- Don't delegate a single known-file read — delegation overhead can cost more than doing it
  directly.
- Never repeat work a subagent already did.

## Step 4 — token hygiene (all models)

- Read only the needed line ranges, not whole files. Grep before Read.
- Ask subagents for summaries, not raw dumps.
- Don't re-read a file you just wrote or edited.
- No restating the question, no recap of steps, no filler. Finish with outcome + next step.
- One-line answers for one-line questions.
- `/clear` between unrelated tasks; `/compact` at natural breakpoints — long context is the
  biggest silent cost.
- Batch independent tool calls in one turn.
- Prefer a script over repeated manual tool calls for bulk or repetitive work.
- Trim CLAUDE.md and unused MCP servers/skills — they load every session.

## Step 5 — verify routing actually works

`model:` on a subagent can silently fail on some Claude Code builds. Once per project, run one
trivial Haiku subagent and check its transcript
(`~/.claude/projects/<proj>/<session>/subagents/agent-*.jsonl`, grep "model"). If it ran on the
session model instead, tell the user and fall back to manual `/model` switching for that
session.

## Step 6 — measure (monthly, or when limits hit early)

```
python3 ~/.claude/skills/token-optimizer/scripts/token-audit.py --days 14
```

(swap the path for wherever you installed this folder). Read-only, local, stdlib-only, no
network. Read the `share` column per model and the top sessions. On a subscription plan the
`$` column is only a relative guide, not a bill. If Opus's share is high on mechanical work,
tighten routing; if one session dominates, it grew too long — `/clear` earlier. Also check
`/context` for MCP servers, skills, and plugins loaded every session and disable what's unused.

## Honest limits

- Savings are large on API billing, smaller on Pro/Max subscriptions.
- Haiku's tokenizer is verbose — price per token isn't price per job. Measure on real tasks.
- Quality gate: if a cheap-tier result is shaky, redo it one tier up. Don't ship doubtful output.

## Looking for hard enforcement instead?

This skill is advisory — it works by instruction, like any other skill, so it can be skipped
under pressure. The same project also ships a `PreToolUse` hook plugin that structurally
blocks direct Bash/Write/Edit at the top level instead of asking nicely. See the main
[README](../../README.md) (or this project's root, if you copied this folder out on its own)
for that option.
