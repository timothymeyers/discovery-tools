# Billing multipliers (feature-flagged)

**Only used when the user explicitly requests cost with `--with-cost`.**

Multipliers are GitHub Copilot premium-request multipliers, recovered from the
ACP model catalog in `.discovery/engine/copilot-cli/logs/*/copilot-stdout.log`.
Machine-readable version: `billing_multipliers.json`.

| Multiplier | Models |
|---|---|
| 0× | `gpt-5-mini` |
| 0.33× | `claude-haiku-4.5`, `gpt-5.4-mini` |
| 1× | `claude-sonnet-5`, `gpt-5.4`, `gpt-5.6-sol`, `gemini-3.1-pro`, `grok-4.5` |
| 3× | `claude-opus-4.6` |
| 7.5× | `claude-opus-4.7`, `gpt-5.5` |
| 14× | `gemini-3.5-flash`, `gemini-3.6-flash` |
| 15× | `claude-opus-5`, `claude-opus-4.8` |

## Notes

- **Cost model:** premium units per request = multiplier × request count. This
  is the model the Clio `ShutdownModelMetric.cost` field uses (source B).
- **Cache decision:** Clio's recorded `cost` counts cached-read requests as
  full-billable. Do not silently discount cached reads — surface the cache hit
  rate separately so the user can decide.
- **Missing from catalog:** `gpt-4o-mini-2024-07-18` appears in TokenJournal
  (source A) but is not in the ACP catalog. The miner prints its name and
  skips cost for it.
- **Refresh:** if new models appear, re-read a recent
  `.discovery/engine/copilot-cli/logs/*/*/copilot-stdout.log` — the catalog
  is emitted as `available_models` in the ACP init exchange. Look for
  `"billing":{"multiplier":N}` per model.
