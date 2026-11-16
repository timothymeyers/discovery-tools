---
name: discovery-status-board
description: |
  Cross-domain / Research platform observability — Strictly read-only "Status"
  board for a Microsoft Discovery project. Top bar shows every Outcome as a
  red / yellow / green chip from its latest grade against the rubric's
  passingCriteria.minimumScore (hollow if never graded); clicking a chip
  highlights that outcome's tasks. Below it, a kanban (To Do | In Progress |
  Waiting | Review | Done) of leaf tasks in collapsible swimlanes that follow
  the task hierarchy. Waiting cards show why; To Do cards with unmet
  dependencies are hatched as "not ready". Uses the Engine Chat theme. WHEN:
  "status board", "project status", "kanban", "show outcomes and tasks",
  "outcome grades", "open the status dashboard".
metadata:
  version: "1"
  category: "Cross-domain"
  subfield: "Research platform observability"
---

# discovery-status-board

Read-only. Never writes to `.discovery/` or anywhere in the workspace.

## Inputs (all under `<workspace>/.discovery/`)

| Data | Path |
|---|---|
| Outcomes | `outcomes/*.json` (`title`, `taskIds`, `rubricId`) |
| Rubrics | `rubrics/*.json` (`questions[].weight`, `passingCriteria.minimumScore`) |
| Grades | `grades/<outcomeId>/*.json` — latest by `createdAt` |
| Tasks | `tasks/taskentries/*.json` (`parentId`, `siblingOrder`, `status`, `dependsOn`) |

## Rules

- **Outcome color**: weighted mean of latest grade's `normalizedScore` (rubric
  weights). green ≥ minimum; yellow ≥ minimum − 0.25; red below; hollow if no grade.
- **Columns**: `new`→To Do, `executing`→In Progress, `executionDone`→Review,
  `complete`→Done, anything else→Waiting (with reason tag, e.g. `flaggedHuman`
  → "Needs human input").
- **Not ready**: To Do cards whose `dependsOn` include a non-complete task are
  hatched and tagged "waits on DX-…".
- **Swimlanes**: one per non-leaf task, nested; leaf tasks are cards. A single
  top-level task becomes the page title. Lane headers show per-column counts;
  collapse state persists in localStorage.
- **Outcome link**: a task belongs to an outcome if it or an ancestor is in the
  outcome's `taskIds`.

## Run

Resolve this loaded `SKILL.md` file's parent directory as `SKILL_DIR`. Never
assume a checkout location.

Live server (re-reads on every refresh), bound to 127.0.0.1. Run in an
**async** terminal (do not wrap in `timeout`):

```bash
python3.12 "$SKILL_DIR/scripts/status_board.py" serve --workspace <ws> --port 8766
```

Then open `http://127.0.0.1:8766/` in the integrated browser. The page polls
`/data.json` every 30 s and re-renders in place only when data changed,
preserving lane collapse state, the outcome filter and scroll position. Add
`?clawpilotTheme=dark|light` to force a theme.

Other modes: `render --out <file outside workspace>` (snapshot), `json` (model dump).
