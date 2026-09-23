# The telemetry contract

Every top-line number the dashboard reports has exactly one definition, stated
here and implemented once in `scripts/collect.py`. Panels never compute their
own. Read this before changing collector logic.

These rules are conservative on purpose. Where evidence is ambiguous the
collector reports **unknown** rather than guessing, because a dashboard that
quietly reports a plausible wrong number is worse than one that admits a gap.

This contract was reconciled from four independently-built dashboards that did
**not** agree with each other. The conflicts are recorded below, because the
disagreements are the most valuable part.

---

## Task status

Raw status casing varies by source: `tasks/index.json` and
`tasks/taskentries/*.json` use camelCase (`executionDone`), while
`tasks/status-summary.json` uses PascalCase (`ExecutionDone`). All statuses are
lowercased before use.

- **Approved complete** counts only `complete`.
- **Awaiting review** counts only `executionDone`. It is **not** approval and is
  never summed with approved into a single headline number.
- Progress denominator is **leaf tasks only**, so parents and children are not
  both counted.

> **Conflict resolved.** One source dashboard counted done as `complete` only;
> another counted `complete + executionDone` as a single figure. Those produce
> materially different completion percentages on the same workspace. The split
> presentation wins: merging them silently converts unreviewed work into
> finished work.

Leaves are derived from `graph.json` decomposition edges — a task with no
outgoing `decomposition` edge is a leaf. Dependency edges do **not** make a task
a parent. The `leaf` label is a fallback used only when the graph is unreadable,
and that fallback is reported as `partial` coverage because labels can be stale.

## Dependencies

The field is **`dependsOn`**. `dependencies` is not a valid projection — the
tasks tool rejects it — and is never read.

- A dependency is satisfied by either `complete` or `executionDone`, matching
  Discovery's own readiness semantics.
- A `dependsOn` reference to a task that cannot be found **remains a blocker**
  and is recorded as an unresolved reference. Missing is not satisfied.

## Task identity

`name` (or `taskId` in the index projection) is a UUID. `dxId` is the human
reference. Both are carried; all joins are on the UUID. `parentId` accepts
DX-ids in the tasks tool but stores UUIDs on disk.

## Engine liveness

Evaluated in strict precedence order:

| Condition | State |
|---|---|
| `completedAt` present | `finished` |
| no `completedAt`, pid verified live | `running` |
| no `completedAt`, pid verifiably dead | `stopped` |
| anything else | `unknown` |

**Age is never used to diagnose a stall.** `meta.json` `startedAt` and
`run.json` `writtenAtUtc` are startup metadata, not heartbeats. A log file's
mtime is evidence that a log was written, not that an event occurred. The
collector never emits the word "stalled", and a test asserts that.

> **Conflict resolved.** An earlier dashboard's methodology footer claimed
> "running = touched within 120s, idle = quiet under 15m" and downgraded a stale
> run to stalled when its process was gone. That inference is unsound: a quiet
> engine is not a stalled one, and the UI treatment was kept while the claim was
> discarded.

**Process verification requires two facts, not one.** The recorded pid must
exist *and* its observed start time must match `ownerProcessStartedAt` within
tolerance. A live pid with a different start time is pid reuse and yields
`unknown`, never `running` — otherwise a recycled pid reports as a healthy
worker.

A live shared host process does not prove any individual worker is healthy.
Runtime owner pid cannot be attributed to a run without a verified mapping.

**Idle is not finished.** Idle means inactive; `completedAt` is what makes a run
finished.

## Agent run identity

Runs are deduplicated through a **union-find alias graph** over every identifier
they are known by: `runId`, `instanceId`, `sessionId`, `legacyRunId`,
`acpSessionId`, plus the ACP `sessionId` observed inside `copilot-stdout.log`.
`run.json` `instanceId` matches `sessionId`/`legacyRunId`, but the ACP session
that appears in actual tool traffic is only discoverable from stdout.

Single-key dedup is wrong in both directions — it double-counts one run and
collapses unrelated ones.

**Two passes are required.** Unioning while grouping is a real bug: a later
union can re-root an existing group, so a key captured earlier stops resolving
to the same root and one run splits into two. All ids are unioned first, and
only then are records grouped by canonical root.

## Failure classes

A generic `"Agent execution failed. See diagnostics for exception type."` with
no diagnostic means the agent **never started** — a registration or validation
problem, not a task failure. It gets its own class and its own alert kind rather
than being lumped in with ordinary failures.

Related rules:

- Null/NaN results must never sort as though they were values. Non-converged
  cases sorting to the top of a ranking is a known real-world instance.
- Historical errors do not raise current alerts. A bookshelf `start_indexing`
  error was once observed in the *trigger call only*, while `autoIndex` had
  already succeeded.

## CLIO

Detected two ways: a `pluginDirectory` pointing at a clio plugin, and observed
structured tool calls.

In engine exhaust CLIO surfaces as **`clio-<verb>`**, not under its MCP tool id.
Known verbs: `start`, `stop`, `run`, `wait`, `status`, `steer`, `mode`,
`archive`, `disposition`, `investigate`. The verb set is an allowlist because a
bare `clio-\w+` match also catches path fragments like `clio-plugin` and
`clio-stderr`, which are not tool calls.

Tool names are read from the `**<tool>**` head of `ActionProposed` events.
`ActionApplied` echoes the same name and is skipped, or every call doubles.

**Observed is a floor, never a ceiling.** Direct editor invocations and
subagents can be invisible. Zero observed does not mean CLIO was never used.
Distinct ACP sessions and an absence of observed tool calls do not prove
clean-context or filesystem sandbox isolation.

Outstanding investigations are opened-minus-closed **within the scanned
window**. A bounded tail can clip the opening call, so the figure is clamped at
zero and flagged `investigationsEstimated`. It is not a verified live count.

## Events

`Thinking` and `Observation` stream **token-by-token**. Reading one event per
line produces unreadable shrapnel (`"ionable."`, `"is act"`). Consecutive
fragments of the same streaming kind within a run are coalesced back into one
message, timestamped at the **start** of the message.

`ActionProposed`, `ActionApplied`, `Error` and `Done` arrive whole and stay
discrete, so two consecutive tool calls are never merged into one entry.

`Thinking` is dropped entirely — it is reasoning trace, not project state.

Events without a timestamp stay untimestamped rather than borrowing the log
file's mtime, and sort last.

## Git

`.discovery/logs/` and `.discovery/tasks/*.json` churn constantly while
Discovery runs (`rebuiltAt` timestamps, live log appends), so the working tree
is **never clean**. Counting that as project activity is the single biggest
source of "stale and irrelevant information" in a naive dashboard.

`.discovery` paths are excluded from per-commit churn counts and reported
separately as `suppressedDiscoveryFiles`.

A repository with no commits yet is **present and empty**, not a read failure.

## Coverage

Every source lands in exactly one state:

| State | Meaning |
|---|---|
| `ok` | read successfully (may still be legitimately empty) |
| `missing` | not present in this workspace — normal across versions |
| `error` | could not be read; the view is incomplete |
| `partial` | read but bounded or degraded; counts may be low |

Conflating "could not read" with "genuinely empty" is how a dashboard lies
quietly. `error` and `partial` mean the numbers may under-report and should be
surfaced before any figure is quoted.

## Bounded reads

Log tails are capped at 512 KiB, read from the end, discarding the partial first
line. Full-history rescans are what made earlier dashboards re-read gigabytes on
every five-second poll. Truncation is reported, not hidden.

Partial JSON writes are expected rather than exceptional — Discovery rewrites
these files live — and a torn read degrades one source to `partial` instead of
taking the snapshot down.

## Security

Engine `meta.json` carries the **full operator prompt**, which can contain the
entire research brief and environment details. Only a fixed allowlist of scalar
fields leaves the collector. A golden test asserts no prompt text appears in a
snapshot taken from real workspaces.

The HTTP layer serves a fixed route table. No filesystem path is ever derived
from a request, and no raw workspace file, prompt, or log is exposed.

---

## Cross-version schema notes

Verified against Discovery Express 0.15.13 → 0.15.15. Real differences handled:

| Thing | Variation |
|---|---|
| `agent-runs/` | present in 0.15.13 / 0.15.14, **absent** in 0.15.15 |
| catalog logs | both `catalog-<name>/<instance>` and `catalog/<name>/<instance>` occur |
| `sessions/`, `toolcatalog/` | may be gitignored and absent entirely |
| status casing | camelCase in `index.json`, PascalCase in `status-summary.json` |
| dependency field | `dependsOn` only; `dependencies` is rejected |
| task identity | `name` is a UUID, `dxId` is the human reference |
| bookshelf sources | identified by `uri`; there is no `name` field |
| engine events | `Thinking`/`Observation` stream; `Action*`/`Error`/`Done` are whole |

If a future version breaks a source, the failure surfaces in the coverage panel
rather than as a silently wrong number. When adding support for a new version,
add a golden fixture rather than only a synthetic one — synthetic fixtures
cannot catch schema drift, and every drift listed above was found by running
against real workspaces.
