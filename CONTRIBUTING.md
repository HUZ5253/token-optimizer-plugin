# Contributing

This repo doesn't need a full contribution-process document. There's exactly one rule
that matters here, and it's the kind of thing that's easy to forget under a routine commit
unless the reasoning behind it is written down.

## The rule

Bump `version` in `.claude-plugin/plugin.json` in any commit that adds, removes, or
changes a component under `skills/`, `agents/`, `hooks/`, or `commands/`.

- Adding a new component is a **minor** bump (e.g. `1.1.0` → `1.2.0`).
- A fix to an existing component is a **patch** bump (e.g. `1.1.0` → `1.1.1`).

## Why this matters

Claude Code caches an installed plugin by its version string — the cache directory is
literally named after the version
(`.claude/plugins/cache/<marketplace>/token-optimizer-plugin/<version>/`). If the version
field doesn't change, Claude Code has no way to know the plugin changed at all: existing
installs will never receive the update, and `/plugin update` may report them as already
current. This isn't a style preference — the version string is the *only* signal Claude
Code uses to know a plugin needs updating.

## What actually happened

`.claude-plugin/plugin.json` stayed at version `1.0.0` from the plugin's first commit
through 17 audit findings and numerous fixes — every single one of those fixes shipped
under the same version number and never reached any already-installed copy. The gap was
only caught when a later commit added a new skill and a project-scoped install reported
zero skills available, because its cache was still pinned to the stale `1.0.0` directory.
It was fixed in `cea72ab` (bumped to `1.1.0`) — but anyone who installed before that
commit needs to uninstall and reinstall, not update, to actually receive anything that
shipped in between.
