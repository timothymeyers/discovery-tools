# Exhaust sources (what `mine_exhaust.py` reads)

All sources are read-only and optional. Each missing source becomes an empty list plus a `notes[]` entry.

| Source | Location | Used for |
|---|---|---|
| Git | `git log` over the range | commits (subject, redacted body, files, stats, task ids, category) |
| Purpose | `.discovery/purpose.json`, `outcomes/*.json`, `grades/<outcome>/*.json` | setup scene, scope events, gates |
| Tasks | `.discovery/tasks/taskentries/*.json` | units (tree), transitions, reopen counts, dependencies |
| Engine runs | `.discovery/engine-runs/<def>/<id>/{meta.json, output.jsonl}` | runs, dispatches (subagent / Clio / agent-run / cognition) with a file#line locator |
| Clio | `.discovery/science-runs/*.json` | science runs (goal, parent, tool calls, steers) |
| Agent runs | `.discovery/agent-runs/*.json` | catalog agent runs (agent, task, status) |
| Chat | `<VS Code user data>/workspaceStorage/*/chatSessions/*.jsonl` whose `workspace.json` matches the workspace | **human turns and form answers only** |
| Tokens | the `discovery-token-usage` skill's `mine_tokens.py`, when installed, plus chat request metadata | token scene |

## Chat JSONL essentials
- `kind:0` is the snapshot (`v.requests`). A `kind:2` record with `k:["requests"]` appends requests; an optional `i` truncates first. A `kind:1` record with `k:["requests",N,"result"]` carries the token metadata. A `kind:2` record with `k:["requests",N,"response"]` carries `questionCarousel` answers.
- The request text is at `message.text`. Answer values can be a string, `{selectedValue}`, `{selectedValues}` or `{freeformValue}`.
- Each human item gets a locator `{file, line, pointer, sha256}`. `verify_quotes.py` and `build` re-read that line, follow the pointer, and compare both the text and the hash.
- Excluded: system-injected `[Terminal … notification:` turns. `/compact`, `@agent Try Again` and `@agent Continue` are tagged `control`.
- Answers carry their parent request's timestamp (`timeBasis`).

## Traps
- Subagent dispatches can be synchronous and have no run id; they get `dispatchId = file#Lline`.
- An umbrella root task spans the whole project and must not be treated as a unit.
- Outcomes created mid-project are scope changes, not setup.
- The token miner may find no Clio usage even when there are many science runs. This produces a caveat; never show it as zero.
- Copied workspaces can carry another user's engine config and chat storage. Check that `workspace.json` matches before trusting chat.
- Redaction: commit bodies pass through `redact()`, which covers trailer emails and common token and key patterns. It is not a full secret scanner. Chat text is quoted exactly, so the audience-facing excerpt must be chosen with care. Ask the user before showing anything personal.
