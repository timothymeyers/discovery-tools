---
name: discovery-dashboard
description: |
  Cross-domain / Research platform observability — Read-only live operations
  dashboard for a Microsoft Discovery workspace, built by scraping
  `.discovery/` and git. Shows leaf-task progress with approved-complete and
  awaiting-review kept strictly separate, the executing / ready / blocked work
  front from live `dependsOn` edges, engine liveness verified against real
  process identity, agent runs deduplicated across an alias graph, observed
  CLIO investigations, and a coverage panel separating "could not read" from
  "genuinely empty". Never writes to the workspace. WHEN: "project dashboard",
  "discovery dashboard", "what's the status of this project", "what's stuck",
  "how close are we to done", "are the engines still running", "is anything
  running", "are any agents running", "is clio running", "open the dashboard".
metadata:
  version: "2"
  category: "Cross-domain"
  subfield: "Research platform observability"
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
   | "open the dashboard" | `python3 "$DASH" --workspace <ws> --no-open` then open the printed URL in Discovery App's integrated browser |
   | feed another tool | `python3 "$DASH" --workspace <ws> --json` |
   | share a frozen view | `python3 "$DASH" --workspace <ws> --html out.html` |

   **"Open the dashboard" means a tab inside Discovery App, not the system
   browser.** Always pass `--no-open` and then open the loopback URL the server
   prints using the app's integrated browser. Only fall back to the system
   browser if the user asks for it, or if there is no integrated browser
   available.

   Other flags: `--port N` (default 8787, walks forward up to +20 if busy) and
   `--no-open` to suppress the browser. If the requested port is already taken
   the server names what holds it rather than silently moving — a stale
   dashboard from days ago otherwise looks identical to a fresh one.

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
- **CLIO is reported as investigations, not call counts.** Report how many runs
  started, how far they got, and whether they finished. Tool-call tallies are
  deliberately not shown and should not be quoted: "67 of 70 calls were
  `clio-wait`" measures polling frequency, not progress.
- **Investigation state is each run's own account of itself.** A run killed
  without updating its store still reads `running`; the dashboard flags
  **process gone** rather than silently rewriting the state.
- **The CLIO run store is machine-wide.** A run is listed only if its id appears
  in this workspace's engine exhaust, or its recorded goal names this workspace.
  Runs from other projects on the same machine are excluded. Direct editor
  invocations can still be invisible, so zero is not proof CLIO was unused.
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

**Overview** — progress, then a realtime row (executing, awaiting review,
engines), then action needed now, ready, and investigations and agents.
**Blocked by dependencies** sits at the bottom, collapsed: it is not something
you watch live. Collapsed panels keep their open/closed state across
auto-refresh.

**Diagnostics** (collapsed by default) — live log, git activity, purpose and
outcomes, bookshelf, investigation detail, coverage and gaps.

Bookshelf shows, per shelf, the documents on disk and the indexer's own count
**side by side and never merged** — a gap between them means indexing has not
caught up, not that documents were lost. Only document metadata is read.

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
