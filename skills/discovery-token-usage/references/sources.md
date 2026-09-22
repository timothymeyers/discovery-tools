# Source inventory

## Tier 1 — Rich, per-call, verified

| # | Source | What it gives |
|---|---|---|
| **A** | `~/Library/Application Support/DiscoveryApp/telemetry/token-usage.jsonl` | Per-request interactive-plane usage: `model`, `provider`, `capability`, `operation`, real + estimated tokens, `tokenSource`, `latencyMs`, `requestId`, `sessionId`. |
| **B** | `<ws>/.discovery/engine/clio/checkpoint/*/*/*/*/conversation_history.json` → `SESSION_SHUTDOWN` events | Per-model rollup with `cost` (premium units), `requests`, `input`, `output`, `reasoning`, `cache_read`, `cache_write`. |
| **C** | `<ws>/.discovery/engine/copilot-cli/logs/*/*/copilot-stdout.log` | Per-prompt ACP `usage{}` blocks + `usage_update` context events + full model catalog with billing multipliers. |
| **E** | `<ws>/.discovery/engine/clio/instances/*/clio-stderr.log` | Per-invocation Copilot SDK `input`/`output`/`cache_read`/`cache_write`. **Superset of B — this is Clio ground truth.** |

## Tier 2 — Prompts and attribution

| # | Source | What it gives |
|---|---|---|
| **D** | `<ws>/.discovery/engine-runs/*/*/meta.json` | Complete engine prompt text + `state`, `startedAt`, `completedAt`, `adapterKind`, `instanceId`. |
| **D2** | Clio `conversation_history.json` | `SYSTEM_MESSAGE`, `USER_MESSAGE`, `ASSISTANT_MESSAGE` with per-message `model` + `output_tokens`, tool calls, results. |
| **F** | `<ws>/.discovery/logs/sdk.log` | Embedding volume (invisible to token journals), grading run outcomes, bookshelf.ask latency/citations. |
| **G** | `<ws>/.discovery/host-tools/vscode-lm-tools.json` + `llm-router.json` | Fixed prompt overhead (tool schema chars ÷ 4 ≈ tokens injected per request), consumer→deployment routing, per-model context windows. |
| **H** | `<ws>/.discovery/tasks/taskentries/*.json`, `agent-runs/`, `sessions/`, `grades/` | `executorId` / `runId` / `sessionKind` join keys from business outcome back to spend. |

## Tier 3 — Dead ends (skip; verified empty)

| Path | Why it fails |
|---|---|
| `engine/clio/checkpoint/*/telemetry.json` | `select_token_counts` all zero. |
| `clio-events.jsonl` → `clio.token_usage` | Payload is only `timestamp`/`eventType`/`seq`. |
| `grades/`, `outcomes/`, `rubrics/` | Scores/reasoning only — no model/token fields. |
| `bookshelf/*/ingest-state.json` | File manifests only — no LLM metrics. |
| `checkpoints/`, `flows/`, `toolcatalog/`, `runtime/` | Config scaffolding. |

## Two-plane structural insight

Discovery emits token data on two separate planes that barely overlap in
time, model population, or mechanism:

- **Interactive plane** — TokenJournal (source A), models like
  `claude-sonnet-5`, `claude-opus-5`, `gpt-4o-mini`. No cost field.
- **Engine plane** — Clio stderr + shutdown metrics (E, B) and copilot-cli
  ACP (C), models like `gpt-5.4`, `claude-opus-4.6`, `gpt-5.6-sol`. Has cost
  (Clio only).

Summing across planes is legitimate. Summing within a plane is where
double-counting bites (see `traps.md`).
