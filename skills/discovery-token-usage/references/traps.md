# Data-quality traps

Each of these will silently corrupt any naive analysis. The miner in
`scripts/mine_tokens.py` handles all seven — if you edit it, keep these
guards in place.

## 1. Clio stderr lines are emitted exactly twice

Every `Copilot SDK token usage: input=... output=... cache_read=... cache_write=...`
line appears twice per invocation — once from the Python logger, once as a plain
`INFO:` line. Verified: all unique tuples appear with multiplicity 2.

**Guard:** dedupe on the exact `(input, output, cache_read, cache_write)`
tuple per instance. Naive `grep | awk sum` **doubles the engine token count.**

## 2. TokenJournal has corrupt lines from concurrent appends

The JSONL file is written by concurrent processes without a lock, so records
interleave mid-write. In observed workspaces, hundreds of lines can be
unparseable
as JSON.

**Guard:** regex-extract `"field":value` pairs from the raw line. If the
result contains `realInputTokens` or `estimatedInputTokens`, keep it flagged
as `_recovered: true`. Recovery is ~100% on real corruption.

## 3. `router` records duplicate `vscode-otel` records

For every real `vscode-otel` record, a `router` record with `0` tokens exists.
On observed workspaces, thousands of each have been seen.

**Guard:** filter or dedupe on `(requestId, source)`. Counting both inflates
call counts by ~2×.

## 4. Checkpoints are cumulative snapshots

The same `model_metrics` block repeats across every timestamped checkpoint
directory for a session — later snapshots are supersets of earlier ones.

**Guard:** dedupe on `(instance, session_id, model)` and keep the maximum
(largest `input + output`). Summing all snapshots multiplies by the checkpoint
count.

## 5. `SESSION_SHUTDOWN` (B) captures only a subset of stderr (E)

In observed workspaces, B has captured as little as 10–44% of what E showed for the same
instance. B only fires on clean shutdown, and 2 of 12 instances had no
shutdown record at all.

**Guard:** treat E as the volume source. Treat B as a cost-annotation
overlay when `--with-cost` is on. Do not sum B and E.

## 6. `realInputTokens` includes cached reads

The value repeats verbatim across many calls in observed datasets —
`realInputTokens = 144,760` appearing many times over. It is the *context
window size*, not incremental billable input.

**Guard:** prefer `inputTokens` when present. Use `realInputTokens` /
`estimatedInputTokens` as fallback but note the caveat in output.

## 7. `vscode-lm-provider` records are `1`/`1` placeholders

In an observed workspace, thousands of records with `source: "vscode-lm-provider"`
have appeared with exactly `1` input and `1` output token — these are placeholders,
not measurements.

**Guard:** filter records where `source == "vscode-lm-provider"` AND
`tokenSource == "estimate"` AND `input == output == 1`. `mine_tokens.py`
does this via `classify_journal()`.

## 8. Extension-host poll-retry loop (the big one)

A bug in the Discovery App VSCode extension host can cause it to enter a
poll-retry loop that resends the same fixed prompts hundreds of times to
Copilot. Every resend produces a real-looking journal record — same shape
as a normal `vscode-otel` telemetry entry, carrying `realInputTokens` for
the context-window size.

**Observed case:** ~2,600 records for `vscode-otel`/`claude-sonnet-5` in an
83-minute window under one `sessionId`, with only 6 distinct
`realInputTokens` values (three fixed ~140K prompts, each resent ~870
times). Distinct content total: ~750K tokens. Journal reported: ~364M.
**Inflation: ~490×.**

**Diagnostic signal:** group by `(source, model, sessionId)`; a stuck group
has many records but very few distinct `realInputTokens` values.
`mine_tokens.py` flags any group with ≥100 records AND either ≤10 distinct
values OR a records/distinct ratio ≥50.

**Do not use these as signals** — they will burn you:

- `latencyMs == 0` alone. Real `vscode-otel/provider=copilot` records also
  have `latencyMs=0` — it's the default when the extension doesn't measure
  wall time. Only ~half of all `vscode-otel` records are poll-loop artifacts;
  the other half are legitimate real calls.
- `responseCharCount == 0` alone. Same story — the field is often unset.

**Effect if not filtered:** the Interactive plane appears to dominate the
workspace by an order of magnitude and `claude-sonnet-5` looks like the
biggest model consumer. Both are false. The engine plane (Clio 245M input)
is the real workload; the Interactive plane, corrected, is typically a
few million input tokens.

## 9. `ShutdownModelMetric.cost` is NOT `requests × static multiplier`

You might expect `cost / requests` to recover the per-request billing
multiplier for each model. It does not.

**Empirical check** in an observed workspace:

| Model | SHUTDOWN cost | SHUTDOWN requests | `cost / req` | True nominal multiplier |
|---|---:|---:|---:|---:|
| gpt-5.4 | 0.0 | 1,235 | 0.0× | 1× |
| claude-opus-4.6 | 60.0 | 351 | 0.171× | 3× |
| claude-haiku-4.5 | 0.0 | 21 | 0.0× | 0.33× |

The `cost` field appears to be **actual GitHub Copilot billing** with
cache-read discounts, free-tier absorption, and other rules applied — not
a per-request rate. It is genuinely useful as a "what was actually billed"
signal but useless for multiplier derivation.

**Guard:** use the static multiplier catalog (`references/billing_multipliers.json`)
for the nominal per-request rate. Treat SHUTDOWN's `cost` as an independent
"empirical actuals" signal. Never divide one by the other and call it a rate.

`mine_tokens.py` emits both into `token_usage_mined.json` for follow-up:
- `shutdown_cost_per_model` — empirical actuals
- `empirical_multipliers_diagnostic` — the (broken) division, for reference
