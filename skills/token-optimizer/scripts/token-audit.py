#!/usr/bin/env python3
"""token-audit: report real Claude Code token usage and cost from local transcripts.

Reads assistant-message `usage` blocks from ~/.claude/projects/**/*.jsonl (the files
Claude Code itself writes), so numbers reflect actual billing inputs — including
prompt-cache reads/writes — rather than tool-side counters or counterfactuals.

Stdlib only. Usage:
    token-audit.py [--claude-dir DIR] [--project SUBSTR] [--days N] [--top N] [--json]
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- Third-party notice ---
# Adapted from bryanvine/token-saver (bin/token-audit), MIT License:
#
#   MIT License
#
#   Copyright (c) 2026 Bryan Vine
#
#   Permission is hereby granted, free of charge, to any person obtaining a copy
#   of this software and associated documentation files (the "Software"), to deal
#   in the Software without restriction, including without limitation the rights
#   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#   copies of the Software, and to permit persons to whom the Software is
#   furnished to do so, subject to the following conditions:
#
#   The above copyright notice and this permission notice shall be included in all
#   copies or substantial portions of the Software.
#
#   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#   SOFTWARE.
#
# Changes made in this file: added the Haiku 5.5 price tier and a per-model token-share
# column. See THIRD_PARTY_NOTICES.md at the repo root for the same notice.
# --- end third-party notice ---
#
# On Pro/Max the $ column is only a relative guide; read the "share" column.
# $/MTok (input, output). Matched by substring of the model id, first match wins.
PRICES = [
    ("fable", 10.0, 50.0),
    ("mythos", 10.0, 50.0),
    ("opus", 5.0, 25.0),
    ("sonnet", 3.0, 15.0),
    ("haiku-5", 0.10, 0.50),  # Haiku 5.5, prompts <=100K tokens (5x above that)
    ("haiku", 1.0, 5.0),
]
CACHE_READ_MULT = 0.1   # of input price
CACHE_W5M_MULT = 1.25   # 5-minute-TTL cache write
CACHE_W1H_MULT = 2.0    # 1-hour-TTL cache write


def price_for(model):
    m = (model or "").lower()
    for key, inp, out in PRICES:
        if key in m:
            return inp, out
    return 5.0, 25.0  # unknown model: assume opus-tier


def new_bucket():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_w5m": 0, "cache_w1h": 0, "requests": 0}


def add_usage(bucket, usage):
    cc = usage.get("cache_creation") or {}
    w5 = cc.get("ephemeral_5m_input_tokens")
    w1 = cc.get("ephemeral_1h_input_tokens")
    if w5 is None and w1 is None:
        # no TTL split available: assume 5m (the API default)
        w5, w1 = usage.get("cache_creation_input_tokens", 0) or 0, 0
    bucket["input"] += usage.get("input_tokens", 0) or 0
    bucket["output"] += usage.get("output_tokens", 0) or 0
    bucket["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
    bucket["cache_w5m"] += w5 or 0
    bucket["cache_w1h"] += w1 or 0
    bucket["requests"] += 1


def cost_of(bucket, model):
    inp, out = price_for(model)
    return (
        bucket["input"] / 1e6 * inp
        + bucket["output"] / 1e6 * out
        + bucket["cache_read"] / 1e6 * inp * CACHE_READ_MULT
        + bucket["cache_w5m"] / 1e6 * inp * CACHE_W5M_MULT
        + bucket["cache_w1h"] / 1e6 * inp * CACHE_W1H_MULT
    )


def nocache_cost_of(bucket, model):
    """What the same traffic would cost if every token were billed as fresh input."""
    inp, out = price_for(model)
    total_in = bucket["input"] + bucket["cache_read"] + bucket["cache_w5m"] + bucket["cache_w1h"]
    return total_in / 1e6 * inp + bucket["output"] / 1e6 * out


def scan(claude_dir, project_filter, since):
    by_model, by_project, by_session = {}, {}, {}
    session_model = {}
    projects_dir = claude_dir / "projects"
    if not projects_dir.is_dir():
        sys.exit(f"error: {projects_dir} not found (is --claude-dir right?)")
    files = 0
    for path in sorted(projects_dir.glob("*/*.jsonl")):
        project = path.parent.name
        if project_filter and project_filter.lower() not in project.lower():
            continue
        if since and datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < since:
            continue
        files += 1
        seen = {}  # message.id -> (output_tokens, model, usage) ; dedup streamed rewrites
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") != "assistant":
                        continue
                    msg = rec.get("message") or {}
                    usage = msg.get("usage")
                    if not usage:
                        continue
                    if since:
                        ts = rec.get("timestamp")
                        if ts:
                            try:
                                when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                if when < since:
                                    continue
                            except ValueError:
                                pass
                    mid = msg.get("id") or rec.get("uuid")
                    prev = seen.get(mid)
                    if prev is None or (usage.get("output_tokens", 0) or 0) >= prev[0]:
                        seen[mid] = (usage.get("output_tokens", 0) or 0, msg.get("model") or "unknown", usage)
        except OSError:
            continue
        skey = f"{project}/{path.stem[:8]}"
        for _, model, usage in seen.values():
            for table, key in ((by_model, model), (by_project, project), (by_session, skey)):
                add_usage(table.setdefault(key, new_bucket()), usage)
            session_model[skey] = model
    return by_model, by_project, by_session, session_model, files


def fmt_tok(n):
    if n >= 1e9:
        return f"{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.0f}k"
    return str(n)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--claude-dir", default=str(Path.home() / ".claude"))
    ap.add_argument("--project", help="substring filter on project directory name")
    ap.add_argument("--days", type=int, default=30, help="look-back window (default 30; 0 = all)")
    ap.add_argument("--top", type=int, default=10, help="number of top sessions to show")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=args.days) if args.days else None
    by_model, by_project, by_session, session_model, files = scan(
        Path(args.claude_dir).expanduser(), args.project, since
    )

    total_cost = sum(cost_of(b, m) for m, b in by_model.items())
    nocache_cost = sum(nocache_cost_of(b, m) for m, b in by_model.items())
    tot = new_bucket()
    for b in by_model.values():
        for k in tot:
            tot[k] += b[k]
    total_input = tot["input"] + tot["cache_read"] + tot["cache_w5m"] + tot["cache_w1h"]
    reused = tot["cache_read"] / total_input if total_input else 0.0

    if args.as_json:
        print(json.dumps({
            "files_scanned": files,
            "totals": tot,
            "reused_input_share": round(reused, 4),
            "estimated_cost_usd": round(total_cost, 2),
            "cost_without_caching_usd": round(nocache_cost, 2),
            "by_model": {m: {**b, "cost_usd": round(cost_of(b, m), 2)} for m, b in by_model.items()},
            "by_project": by_project,
            "top_sessions": [
                {"session": s, "model": session_model.get(s), **b, "cost_usd": round(cost_of(b, session_model.get(s)), 2)}
                for s, b in sorted(by_session.items(), key=lambda kv: -cost_of(kv[1], session_model.get(kv[0])))[: args.top]
            ],
        }, indent=2))
        return

    window = f"last {args.days} days" if args.days else "all time"
    print(f"token-audit — {window}, {files} transcript file(s)"
          + (f", project≈'{args.project}'" if args.project else ""))
    if not files:
        print("no transcripts matched.")
        return
    print()
    grand = sum(b["input"] + b["output"] + b["cache_read"] + b["cache_w5m"] + b["cache_w1h"] for b in by_model.values()) or 1
    print(f"{'model':<22}{'req':>6}{'input':>9}{'output':>9}{'cache-rd':>10}{'cache-wr':>10}{'cost':>10}{'share':>8}")
    for m, b in sorted(by_model.items(), key=lambda kv: -cost_of(kv[1], kv[0])):
        wr = b["cache_w5m"] + b["cache_w1h"]
        print(f"{m[:21]:<22}{b['requests']:>6}{fmt_tok(b['input']):>9}{fmt_tok(b['output']):>9}"
              f"{fmt_tok(b['cache_read']):>10}{fmt_tok(wr):>10}{'$' + format(cost_of(b, m), '.2f'):>10}"
              f"{(b['input'] + b['output'] + b['cache_read'] + b['cache_w5m'] + b['cache_w1h']) / grand:>8.0%}")
    print()
    print(f"reused input (served from cache): {reused:.1%} of all input tokens")
    print(f"estimated cost:                   ${total_cost:,.2f}")
    print(f"same traffic without caching:     ${nocache_cost:,.2f}  "
          f"(caching saved ~${nocache_cost - total_cost:,.2f})")
    print()
    print(f"top {args.top} sessions by cost:")
    ranked = sorted(by_session.items(), key=lambda kv: -cost_of(kv[1], session_model.get(kv[0])))
    for s, b in ranked[: args.top]:
        m = session_model.get(s, "unknown")
        print(f"  {'$' + format(cost_of(b, m), '.2f'):>9}  {s}  ({m}, {b['requests']} req)")


if __name__ == "__main__":
    main()
