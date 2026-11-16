# Proposal format

One file per proposal: `.discovery/setup/proposals/<YYYYMMDD-HHMM>-<slug>.md`.
Sub-skills return ops in this format; the router merges them.

## Template

```markdown
# Proposal 20261112-1430-initial-setup
Mode: full | guided:<step> | update
Inputs: grant.pdf (sha256:ab12…), chat 2026-11-12
Status: pending        # pending | approved | partial | applied | rejected

## Summary
<3–6 lines: what changes and why>

## Purpose
- [ ] P1 SET purpose
  - title: …
  - statement: …
  - why: [grant.pdf §1]

## Data
- [ ] D1 STAGE grant.pdf -> shelf "genesis-gtm" (pending shelf S1)
- [ ] D2 COPY ~/x/runs.csv -> data/runs.csv (2.1 MB)
- [ ] D3 TRACK "ERCOT historian" -> data/external-sources.md (access-pending)

## Bookshelf
- [ ] S1 CREATE shelf "genesis-gtm"
- [ ] S2 INGEST staged/grant.pdf -> S1   (depends: S1)

## Outcomes
- [ ] O1 CREATE "…" type=Measurement
  - evidence: …
  - rubric: draft (3 questions)
  - why: [grant.pdf Aim 1]
- [ ] O2 SUPERSEDE <id> "old title" -> O1   (update mode only)

## Tasks
- [ ] T1 CREATE root "…"
  - validation: …
- [ ] T2 CREATE milestone "…" parent=T1 link=O1
  - validation: … (from O1's rubric)
- [ ] T3 CREATE leaf "…" parent=T2 deps=[] labels=[pattern:guided, access-pending]
  - validation: …
- [ ] T4 CREATE pause "Human review: …" parent=T2 deps=[T3] labels=[hitl-pause, hitl:cost] assign=user
  - validation: decision recorded as a comment with approver and date
  - why: …
- [ ] T9 REMOVE <id> "…"   reason: …

## Tools / agents / skills   (Step 5 proposals only)
- [ ] K1 VENDOR skill <repo>@<sha>:<path> -> .github/skills/<name>
  - runs code: no | network: no
- [ ] K2 ADD MCP server "<name>" (stdio: `uvx foo-mcp`)
  - runs code: YES — command shown above | secrets: FOO_TOKEN (env)
- [ ] K3 REGISTER A2A agent "<name>" <url> -> proxy .github/agents/<name>-remote.agent.md

## Questions
1. …
```

## Ops

| Op | Meaning |
|---|---|
| `SET` / `CREATE` | New artifact |
| `UPDATE` | Change fields; show `before -> after` |
| `KEEP` | Listed in Update mode for completeness; no action |
| `REMOVE` | Tasks only: transition to `removed` |
| `SUPERSEDE` | Outcomes only: see retirement.md |
| `RETIRE` | Shelves, sources, tools: see retirement.md |
| `STAGE` / `COPY` / `TRACK` / `INGEST` | Data placement |
| `VENDOR` / `INSTALL` / `ADD` / `REGISTER` | Step 5 installs |

IDs are local to the proposal (`T3`); `deps`/`parent` may reference them.
Every non-KEEP op needs a `why`. In Update mode, the why quotes the new information.

## Approval syntax (plain chat)

The user can reply with:
- `approve all`
- `approve all except T4, O2`
- `approve P1 O1-O3 T1-T12`
- `change O2: <text>`: revise and re-show only the affected ops
- `reject`

Record the decision in the file: tick `[x]` for approved ops, keep
`[ ]` and add `(declined)` for declined ones, set `Status:`, and append an
`## Approval` section with the verbatim reply and timestamp. Removing
an approved op's dependency also declines the dependent op; tell the
user when that happens.
