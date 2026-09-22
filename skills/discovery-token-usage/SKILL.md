---
name: discovery-token-usage
description: |
  Mine a Discovery App workspace's .discovery/ exhaust for token usage across
  engines (Clio / Science Engine, copilot-cli / Mission Control / Generic
  Copilot), models, and interactive chat. Produces a console read-out by
  default; can also emit a markdown report on request. Cost / billing-multiplier
  data is a FEATURE FLAG — never surface it unless the user explicitly asks
  ("with cost", "include cost", "--with-cost", "premium units"). Handles seven
  data-quality traps (double-emitted stderr, corrupt journal appends, cumulative
  checkpoints, router duplicates, cached-read-in-realInput, etc). WHEN:
  "token usage", "how many tokens", "tokens in/out", "Discovery exhaust",
  "engine spend", "Clio vs copilot-cli", "mine .discovery/", "engine token
  read-out", "which model used the most tokens", "prompt inventory",
  "session_shutdown metrics", "TokenJournal".
---

# discovery-token-usage — Discovery App Token Miner

Mine `.discovery/` exhaust from any Discovery workspace and produce a
comprehensive read-out of token usage across engines, models, and
interactive chat.

## When to use

Trigger when the user asks anything like:

- "Review token usage in this Discovery workspace"
- "How many tokens did the science-engine burn?"
- "Give me a read-out of tokens by model / by engine"
- "Mine .discovery/ for token info"
- "Compare Clio vs copilot-cli spend"
- "What prompts did the engines run?"
- "Show me the TokenJournal / SESSION_SHUTDOWN metrics"

Do NOT trigger for:

- General Azure or GitHub Copilot billing questions (no `.discovery/` involved)
- Interactive-only chat token questions divorced from Discovery App

## Vocabulary (be precise when the user asks)

Discovery emits telemetry at three different granularities. The miner
output uses these terms exactly:

| Term | Means | Source | Typical ratio |
|---|---|---|---|
| **run** | One engine execution (like "Complete DX-3 subtree") | `engine-runs/*/meta.json` | 1 |
| **SDK call** | Clio entering the Copilot SDK once | `clio-stderr.log` (1 line per invocation) | ~10 per run |
| **msg** | One `ASSISTANT_MESSAGE` event (per-model attribution comes from these) | `conversation_history.json` | ~40 per SDK call |
| **API req** | One HTTP request to a model (used for `--with-cost` × static multiplier) | `SESSION_SHUTDOWN.ShutdownModelMetric.requests.count` | ~10 per SDK call |
| **prompt** | One copilot-cli ACP prompt-response cycle | `copilot-stdout.log` `usage{}` blocks | coarser than SDK call |
| **call** (Interactive) | One TokenJournal record | `token-usage.jsonl` (1 record per LLM interaction) | 1:1 with API req |

**Per-model attribution:** ASSISTANT_MESSAGE events give ~100% coverage
(matches stderr output within 0.2% in observed workspaces) and catch
models that SHUTDOWN's rollup can miss. Per-model input is apportioned
from the true stderr total by output-token share — approximate but honest.

**Per-model cost (feature-flagged):** static multipliers from
`references/billing_multipliers.json` × SHUTDOWN's per-model request count.
SHUTDOWN's own `cost` field is empirical GitHub Copilot billing (with cache
discounts and free-tier absorption applied) and is NOT a clean multiplier
signal — see `references/traps.md#9`. It IS emitted into
`token_usage_mined.json` under `shutdown_cost_per_model` for reference,
but the terse output uses the static-multiplier calculation only.

## Default behavior (do this every time unless told otherwise)

1. **Resolve the workspace.** If the user names a path, use it. Otherwise use
   the current working directory. Verify `<workspace>/.discovery/` exists
   before running — if not, tell the user and stop.

2. **Resolve the miner script — once per session.** This skill can be installed
   as a personal skill, a project skill, or as part of a plugin, so never
   hardcode its location. Run this first and reuse `$MINER` for every later
   invocation:
   ```bash
   MINER=$(find ~/.copilot/skills ~/.copilot/installed-plugins ~/.agents/skills \
                .github/skills .claude/skills .agents/skills \
                -name mine_tokens.py -path '*discovery-token-usage*' \
                2>/dev/null | head -1)
   echo "$MINER"
   ```
   If `$MINER` is empty, tell the user the skill payload could not be located
   and stop.

3. **Run the miner with tokens-only output:**
   ```bash
   python3 "$MINER" <workspace>
   ```
   This prints a **terse** read-out (~15 lines): per-engine top-line plus
   per-model breakdown for Clio, copilot-cli, and Interactive planes, plus an
   embedding-count one-liner. Full detailed tables (A–H2) are silently emitted
   into `token_usage_mined.json` **in the current working directory** for
   follow-up work.

3. **Read the poll-loop callout aloud if present.** The miner will surface
   any `⚠ suppressed N records matching the Discovery App poll-loop signature`
   line — mention this to the user briefly so they know Interactive-plane
   numbers are the *real* counts, not the raw journal totals. Do not go into
   detail on the bug unless asked.

4. **Do NOT elaborate on:** the two-plane framing, cache weak spots per agent,
   fixed prompt overhead (79 tools ≈ 29K tokens), the RFI-corpus program
   narrative from prompt text, or which engine outweighs which. Those go
   into the report/verbose output — never in the default summary.

5. **After the read-out is delivered, offer the markdown report ONCE:**
   > "Want me to also write a full markdown report to the workspace?"

   If yes, re-run with `--report <path>` where `<path>` defaults to
   `<workspace>/token-usage-report-YYYY-MM-DD.md`.

## Verbose mode (only when the user asks for detail)

If the user says "give me the detail" / "show the full tables" / "verbose" /
"drill down", add `--verbose`:
```bash
python3 "$MINER" <workspace> --verbose
```
This adds the A/B/C/D/E/F/G/H/H2 tables. Do not offer this by default.

## Feature flag: cost / billing multipliers

**HARD RULE: never mention cost, premium units, dollars, multipliers, or
"how much this costs" in default output. Do not offer it. Do not hint at it.**

Cost is available ONLY when the user explicitly asks. Trigger phrases (must
be from the user, not inferred):

- "include cost", "with cost", "add cost"
- "premium units", "billing", "dollars"
- "--with-cost"

When explicitly asked, re-run with the flag:
```bash
python3 "$MINER" <workspace> --with-cost
```
In terse mode this adds a `· N.N units` suffix to each per-model row using
the multipliers in `references/billing_multipliers.json`. In `--verbose` mode
it also adds:
- `cost` columns to the SESSION_SHUTDOWN table (from `ShutdownModelMetric`)
- A dedicated H3 "Derived cost per model" table

If a model in the usage data is not in the multiplier table, the row shows
`?` for the cost column (do not guess).

## Known Discovery App bug: extension host poll-retry loop

There is a bug in the Discovery App VSCode extension host where it can enter
a poll-retry loop that resends the same 1–5 fixed context windows hundreds
of times to Copilot. All the resends carry `realInputTokens` (context-window
size), so a naive read of the TokenJournal inflates the Interactive-plane
input count by hundreds of times.

**Observed case:** ~2,600 `vscode-otel`/`claude-sonnet-5` records in an 83-minute
window under one `sessionId`, with only 6 distinct `realInputTokens` values.
Distinct content: ~750K tokens. Journal claimed: 364M. **Inflation: ~490×.**

**Detection heuristic** (implemented in `classify_journal()`):
group by `(source, model, sessionId)`. If a group has ≥100 records AND
either ≤10 distinct `realInputTokens` values OR a records/distinct ratio
≥50, treat the entire group as suppressed poll-loop noise.

Do NOT rely on `latencyMs=0` alone — it turns out `latencyMs=0` is normal
for `vscode-otel` records with `provider=copilot`, even for real completed
calls. The distinctness-of-`realInputTokens` signal is what's diagnostic.

If the miner emits `⚠ suppressed N records matching the Discovery App
poll-loop signature`, mention that briefly to the user so they know the
Interactive numbers are the real ones. Suppressed records are still in
`token_usage_mined.json` under `journal_classification.idle_windows`.

## Combined invocation

```bash
# tokens + cost + markdown report, all together (only when user asked for cost)
python3 "$MINER" \
    <workspace> --with-cost --report <workspace>/token-usage-report.md
```

## Sources mined (Tier 1)

| # | Source | What it gives |
|---|---|---|
| A | `~/Library/Application Support/DiscoveryApp/telemetry/token-usage.jsonl` | Per-request interactive-plane usage. `model`, `provider`, `capability`, `operation`, real+estimated tokens. |
| B | `<ws>/.discovery/engine/clio/checkpoint/*/*/*/*/conversation_history.json` → `SESSION_SHUTDOWN` | Per-model rollup with **cost** (premium units). Subset of E. |
| C | `<ws>/.discovery/engine/copilot-cli/logs/*/*/copilot-stdout.log` | Per-prompt ACP `usage{}` + model catalog with **billing multipliers**. |
| D | `<ws>/.discovery/engine-runs/*/*/meta.json` | Complete engine prompt text + `adapterKind`. |
| E | `<ws>/.discovery/engine/clio/instances/*/clio-stderr.log` | **Per-invocation ground truth** for Clio tokens. Superset of B. |
| F | `<ws>/.discovery/logs/sdk.log` | Embedding volume (invisible everywhere else) + grading runs. |
| G | `<ws>/.discovery/host-tools/vscode-lm-tools.json` + `llm-router.json` | Fixed prompt overhead, model→deployment routing, context windows. |

See `references/sources.md` for the full tier list and dead-end paths to skip.

## Data-quality traps (all handled by the miner — do not defeat them)

The miner already handles these. If you edit the script, keep these guards:

1. **Clio stderr lines are emitted twice** (Python logger + plain `INFO:`).
   The miner dedupes on the exact `(input, output, cache_read, cache_write)`
   tuple. Naive `grep | sum` double-counts.
2. **TokenJournal has corrupt lines** from concurrent unlocked appends.
   The miner regex-salvages field pairs (100% recovery on the reference
   dataset's 333 bad lines).
3. **`router` records duplicate `vscode-otel` records** with zero values.
   Filter or the call count doubles.
4. **Checkpoints are cumulative snapshots** — same `model_metrics` block
   repeats. Dedupe on `(instance, session_id, model)` and keep the max.
5. **`SESSION_SHUTDOWN` (B) is a strict subset of stderr (E).** Only fires
   on clean shutdown. Use E for volume; treat B as a cost-annotation
   overlay when `--with-cost` is set.
6. **`realInputTokens` includes cached reads** and is context-window size,
   not incremental billable input.
7. **`vscode-lm-provider` records are `1`/`1` placeholders** — filter on
   `tokenSource == "provider"` for quantitative work.

Full detail: `references/traps.md`.

## Interpretation guidance (only when asked)

If (and only if) the user asks for context or interpretation beyond the
default terse output, these are the useful framings:

- **Two disjoint telemetry planes.** Interactive (TokenJournal) and Engine
  (Clio/copilot-cli) barely overlap in time or model population. Summing
  across planes is fine; summing within a plane is where double-counting
  bites (see `references/traps.md`).
- **Instrumentation asymmetry.** Clio has explicit cost and full conversation
  replay. Copilot-cli has context-window pressure telemetry Clio lacks.
  Neither is a superset.
- **"LLM calls" is not like-for-like** across Clio and copilot-cli — one Clio
  invocation and one ACP prompt are different units of work. **Token totals
  are directly comparable.**
- **Fixed prompt overhead.** ~29K tokens of tool schemas are injected into
  every request. Explains large per-call input counts.
- **Cache weak spots.** In verbose output, agents like `catalog-agentic-grader`
  can show low cache hit rates. Do not surface unless asked.

## Output paths

- Console: stdout (default deliverable)
- JSON dataset: `<script_dir>/token_usage_mined.json` (always written; useful
  for follow-up analysis but not shown to user unless asked)
- Markdown report: only when `--report <path>` is passed (offer after console
  read-out is delivered)
