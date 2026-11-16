# Gather current state

Always gather before drafting. Prefer the Discovery tools (read-only
variants) over reading files; use files to confirm and for the ledger.

## Where state lives (all inside the workspace)

| Area | Tool (read) | On disk |
|---|---|---|
| Purpose | `purpose_read get` | `.discovery/purpose.json` |
| Outcomes | `purpose_read outcome.list` | `.discovery/outcomes/<id>.json` |
| Rubrics | `purpose_read rubric.list` | `.discovery/rubrics/` |
| Grades | `purpose_read grade.list` | `.discovery/grades/` |
| Tasks | `tasks_read list` / `graph` | `.discovery/tasks/` |
| Bookshelf | `bookshelf list` | `.discovery/bookshelf/shelves.json`, `<shelfId>/ingest-state.json`, `<shelfId>/ingest-runs/` |
| Bookshelf index | — | `.discovery/bookshelf/providers/` (large; not tracked) |
| Tool catalog | `tools list` | `.discovery/toolcatalog/` (transient; not tracked) |
| MCP servers | — | `.vscode/mcp.json` |
| Agents | `agents list` | `.github/agents/`, `.discovery/config.json` (`agents`) |
| Skills | — | `.github/skills/` |
| Data | — | `data/README.md`, `data/external-sources.md` |
| Setup ledger | — | `.discovery/setup/setup.json`, `proposals/` |

Files appear lazily: a new project may have none of purpose.json,
outcomes/, or shelves.json. Absence means "not set", not an error.

## Snapshot

Produce a compact summary for yourself (and show it to the user in Update
mode):

```
Purpose: <title> | (not set)
Outcomes: N active, M superseded  [titles]
Tasks: root <title>; X milestones, Y leaves; statuses {...}
Shelves: <name> (k docs, ingest status) | none
Staged docs: [...]
Data: d datasets, e external sources (f access-pending)
Agents/skills/MCP: [...]
Last proposal: <id> (<applied|pending|partial>)
```

## Pending work from earlier runs

Check `setup.json` for: staged documents not yet ingested, proposals left
pending, `access-pending` tasks. Surface these first in Update mode.
