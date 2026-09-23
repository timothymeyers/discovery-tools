---
name: discovery-dashboard
description: |
  Stand up a read-only live operations dashboard for a Microsoft Discovery
  workspace by scraping its `.discovery/` directory and git. Shows leaf-task
  progress with approved-complete and awaiting-review kept strictly separate,
  the executing / ready / blocked work front derived from live `dependsOn`
  edges, engine liveness verified against real process identity, agent runs
  deduplicated across an alias graph, observed CLIO investigations, a typed
  live log, git activity with `.discovery` churn filtered out, and an explicit
  coverage panel separating "could not read" from "genuinely empty". Never
  writes to the workspace. Portable across Discovery Express 0.15.13-0.15.15.
  WHEN: "project dashboard", "discovery dashboard", "what's the status of this
  project", "what's stuck", "how close are we to done", "are the engines still
  running", "is anything running", "are any agents running", "is clio running",
  "any investigations running", "open the dashboard", "show me project status".
metadata:
  version: "1"
---

# discovery-dashboard — Discovery workspace live operations

A read-only view of what is actually happening in a Discovery workspace.

Its design goal is **not** to show everything available. It is to answer four
questions honestly: how close are we, what is stuck, what is actually running,
and what can this view not see.

## Resolve the payload — once per session

This skill can be installed as a personal skill, a project skill, or as part of
a plugin, so never hardcode its location. Run this first and reuse `$DASH`:

```bash
DASH=$(find ~/.copilot/skills ~/.copilot/installed-plugins ~/.agents/skills \
            .github/skills .claude/skills .agents/skills \
            -name discovery_dashboard.py -path '*discovery-dashboard*' \
            2>/dev/null | head -1)
echo "$DASH"
```

If `$DASH` is empty, tell the user the skill payload could not be located and
stop. Standard library only — there is nothing to install.

## Default behavior

1. **Resolve the workspace.** If the user names a path, use it. Otherwise use
   the current working directory. If `<workspace>/.discovery/` does not exist,
   say so and stop — this skill has nothing to read.

2. **Pick a mode.** Prefer `--once` when the user asked a question in passing;
   only start a server when they want to look at something.

   | Intent | Command |
   |---|---|
   | "what's stuck?" / "where are we?" | `python3 "$DASH" --workspace <ws> --once` |
   | "open the dashboard" | `python3 "$DASH" --workspace <ws>` |
   | feed another tool | `python3 "$DASH" --workspace <ws> --json` |
   | share a frozen view | `python3 "$DASH" --workspace <ws> --html out.html` |

   Other flags: `--port N` (default 8787, walks forward up to +20 if busy) and
   `--no-open` to suppress the browser.

3. **Report with the caveats attached.** See *Reading the numbers* below. The
   caveats are the product; a bare number from this dashboard is a misuse of it.

4. **Check the coverage panel before trusting anything.** If `--json` shows any
   `error` or `partial` entry under `coverage`, the view is incomplete and the
   counts may read low. Surface that first, before answering the question.

## Stopping it

Killing the shell wrapper does **not** stop the server. It keeps listening, and
the next start fails with `Address already in use`. Kill the listener by pid:

```bash
lsof -nP -iTCP:8787 -sTCP:LISTEN -t
```

## Reading the numbers

Repeat these when reporting figures.

- **Approved complete** counts only `complete`. **Awaiting review** counts only
  `executionDone`, which is *not* approval. They are never summed into one
  "done" number, and neither should you.
- Progress uses **leaf tasks only**, so a parent and its children are not both
  counted.
- **Engine liveness** requires the recorded pid to exist *and* its observed
  start time to match the record. A matching pid with a different start time is
  pid reuse and reports `unknown`. `unknown` is a real answer, not a failure.
- **Age never diagnoses a stall.** Task mtime is not a heartbeat; `startedAt`
  and `writtenAtUtc` are startup metadata; a log's mtime means a log was
  written, not that an event occurred. This dashboard will not tell you
  something is stalled, because it cannot honestly know that.
- **CLIO figures are a floor, not a census.** They count observed structured
  `clio-*` calls inside bounded log tails. Direct invocations and subagents can
  be invisible. Zero observed does not mean CLIO was unused. Outstanding
  investigations are opened-minus-closed *within the scanned window* and are
  labelled estimated — do not report them as a live count.
- **Blockers** come from live `dependsOn` edges. A dependency that cannot be
  resolved stays a blocker rather than being assumed satisfied. Both `complete`
  and `executionDone` satisfy one.
- **Alerts are three separate buckets** — action needed now, blocked by
  dependencies, awaiting review. Only the first means a human must do something.
  A single merged "needs attention" count is noise.
- **Coverage** distinguishes *could not read* from *genuinely empty*. `missing`
  is normal; sources legitimately differ across Discovery versions.

Full definitions, the cross-version schema notes, and the reasoning behind each
rule are in [`references/telemetry-contract.md`](references/telemetry-contract.md).
Read it before changing any collector logic.

## Layout

**Overview** — progress, the three alert buckets, the work front, engines,
agents and CLIO.

**Diagnostics** (collapsed by default) — live log, git activity, purpose and
outcomes, bookshelf ingest, coverage and gaps.

## Safety

Read-only by construction. Never writes to the workspace, never starts or stops
engines or agents, never changes task state. Binds to loopback only. Serves a
fixed route table — no filesystem path is ever derived from a request. Engine
`meta.json` carries the full operator prompt; it is never emitted, and a test
asserts that against real workspaces.

## Development

```bash
python3 -m unittest discover -s scripts -p 'test_dashboard.py' -v   # 52 tests
python3 scripts/build.py           # regenerate dist/discovery_dashboard.py
python3 scripts/build.py --check   # CI runs this; fails if dist/ is stale
```

`dist/discovery_dashboard.py` is generated from `scripts/`. **Re-run the build
after editing anything in `scripts/`** — the runtime users invoke is the
generated file.

The suite is fixture-driven and additionally runs golden tests against real
`~/discovery` workspaces when present, which is what catches schema drift
between Discovery Express versions. Those skip cleanly when none exist.
