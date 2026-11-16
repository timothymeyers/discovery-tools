---
name: discovery-project-setup
description: |
  Cross-domain / Research project setup — Set up a new Microsoft Discovery
  project, or update an existing one from new information, using a
  propose-then-apply workflow. Covers Purpose, data intake (Bookshelf,
  local datasets, external sources), Outcomes, task breakdown, and agents /
  skills / tools / A2A remote agents. Two modes: "propose a full project"
  (e.g. from an uploaded grant application or proposal) or "walk me through
  it" step by step. Never deletes; retires via conventions. Keeps a git
  ledger of every applied change. USE FOR: 'set up my project', 'set up
  Discovery', 'here is my grant, propose a project', 'start a new project',
  'update the project with this new info', 're-plan the project', 'walk me
  through project setup', 'what's missing from my project setup'. DO NOT
  USE FOR: executing tasks, grading outcomes, or a single narrow change
  that a sub-skill handles alone (discovery-define-outcomes,
  discovery-curate-bookshelf, discovery-provision-tools,
  discovery-work-breakdown).
metadata:
  version: "0.1.0"
  category: "Cross-domain"
  subfield: "Research platform engineering"
---

# discovery-project-setup

Router for creating or updating a Discovery project. Every change goes
through the same loop:

**gather current state -> draft desired state -> write proposal -> user
approves -> ledger commit (before) -> apply approved ops -> verify -> ledger
commit (after)**

## Hard rules

1. **Never mutate Purpose, Outcomes, Tasks, Bookshelf, data/, agents,
   skills, or MCP config without an approved proposal.** The proposal file is
   the gate. Apply only ops marked approved.
2. **Never delete.** Tasks -> `removed`; Outcomes -> superseded convention;
   shelves/sources -> retired convention. See
   [references/retirement.md](references/retirement.md).
3. **Ask questions as plain chat** — a short numbered list, then end your
   turn and wait. Do NOT use the askQuestions / question-modal tool. In a
   non-interactive context (engine, SDK, subagent), write the proposal and
   stop without applying.
4. **Ledger:** commit before and after every apply. Never push. See
   [references/git-ledger.md](references/git-ledger.md).
5. **No secrets in tracked files.** Credentials are referenced by env var or
   Key Vault name only.
6. Scientific content uses LaTeX delimiters (per workspace instructions).

## Step 0 — Detect mode and gather state

Read [references/gather-state.md](references/gather-state.md) and build the
current-state snapshot. Then classify the request:

| Signal | Mode |
|---|---|
| Empty project + user supplies documents / "propose" | **Full proposal** |
| Empty project + "walk me through" or no material | **Guided** |
| Existing state + new info / "update" / "re-plan" | **Update** (diff against current) |
| Request targets one area only | Route straight to that step |

If unclear, ask once: "Propose a full project from what you've given me, or
walk through each step?"

## Steps

Each step produces proposal ops (see
[references/proposal-format.md](references/proposal-format.md)). In
**Guided** mode, propose and approve after each step. In **Full proposal**
mode, draft Steps 1–4 together into one proposal, approve once, then run
Steps 5–6 as their own proposal (installs need a separate trust review).

### Step 1 — Purpose and context
Opening prompt (adapt, keep short):
> Describe what you're working on — what would you like to discover? Attach
> or point me to anything that describes the project (grant application,
> proposal, articles, datasets).

If documents are supplied, read
[references/extract-from-docs.md](references/extract-from-docs.md) and draft
Purpose from them, citing source passages. Stage every supplied document
(Step 2 handles placement). Ops: `purpose.set` / `purpose.update`.

### Step 2 — Data intake
Prompt:
> Anything else on hand we should look at while getting oriented — papers,
> datasets, databases, APIs?

Classify and place every source using the `discovery-curate-bookshelf`
skill (read its SKILL.md; run it in proposal-only mode). Unstructured docs
-> Bookshelf (staged if no shelf exists); structured datasets -> `data/`;
external systems -> `data/external-sources.md`.

### Step 3 — Outcomes
Prompt:
> What are your goals or deliverables? How will you know you've succeeded?

Use the `discovery-define-outcomes` skill in proposal-only mode. Default
4–5 outcomes, up to 10 when the material supports it. Each outcome carries a
type, evidence criterion, source citation, and a draft rubric.

Optional pre-flight: after Step 3, offer `discovery-clarify-project`
(read its SKILL.md; deliver its questions as plain chat, not a modal).

### Step 4 — Task breakdown
Use the `discovery-work-breakdown` skill in proposal-only mode, with the
approved Purpose, Outcomes (and rubrics), existing tasks, and data inventory
as input. It returns root -> milestone per outcome -> as many leaf tasks as
the work needs, with validation requirements on every task, dependencies,
`outcome.link-task` for every milestone, suggested execution patterns,
human-in-the-loop pause tasks before safety-critical or high-cost steps, and
compute chains where the goals need HPC or cloud. Tasks needing an external
source without access are labelled `access-pending`. Show its tree in chat
and iterate until the user approves.

To design one milestone in detail as an investigation for a specific agent
set, offer `discovery-design-tasks` (in `discovery-cape-labs`) after Step 5,
once agents are installed.

### Step 5 — Agents, skills, tools
Use the `discovery-provision-tools` skill. It handles user-supplied sources
(local paths, URLs, GitHub repos, MCP servers, A2A agents) and the catalog
review (microsoft/discovery via `discovery-add-agents`,
timothymeyers/discovery-tools, and any extra catalog repos the user names).
Recommendations must trace to a task or an `access-pending` gap.

### Step 6 — Assign and finalize
Proposal: assign installed agents to leaf tasks (pause tasks stay assigned
to the user); clear `access-pending` labels whose gap is closed; mark
engine-eligible tasks. Then apply, verify
([references/apply.md](references/apply.md)), commit, and present next
steps:
- "Show me my ready tasks" to work interactively.
- Start an engine on engine-eligible tasks.
- Query the bookshelf.

## Files this skill owns

```
.discovery/setup/
  setup.json              # mode, objective, step status, staged items, decisions
  proposals/<id>.md       # one per proposal; approval markers inline
  staged/                 # documents awaiting a shelf
  catalog-cache/          # catalog review results (names/paths/SHA only)
  a2a-agents.json         # registered remote agents (no secrets)
data/
  README.md               # dataset manifest
  external-sources.md     # external databases / APIs / local-only paths
```

## References
- [gather-state.md](references/gather-state.md) — where state lives, snapshot procedure
- [extract-from-docs.md](references/extract-from-docs.md) — grant/proposal -> project draft
- [proposal-format.md](references/proposal-format.md) — op schema and approval syntax
- [apply.md](references/apply.md) — applying approved ops, verification
- [git-ledger.md](references/git-ledger.md) — commit protocol and .gitignore policy
- [retirement.md](references/retirement.md) — non-destructive retirement conventions
