---
name: discovery-work-breakdown
description: |
  Cross-domain / Research methodology and experiment design — Build or
  re-plan a Microsoft Discovery project's task tree from its Purpose,
  Outcomes, rubrics, and existing tasks and statuses: one root, a milestone
  per outcome, and as many leaf tasks as the work needs. Every task at every
  level gets validation requirements and real dependencies. Suggests an
  execution pattern per leaf (deterministic, guided, autonomous), inserts
  human-in-the-loop pause tasks before safety-critical or high-cost steps
  (autonomous-lab protocols, HPC or cloud runs), and plans cloud compute
  when the goals need it. Shows the full shape in chat for feedback and
  approval, then creates it with the tasks tool. USE FOR: 'break down the
  work', 'plan my tasks', 'build the task tree', 'turn my outcomes into
  tasks', 're-plan tasks', 'where should a human sign off', 'do we need
  cloud compute'. DO NOT USE FOR: a standalone investigation design file
  (use discovery-design-tasks), defining outcomes (use
  discovery-define-outcomes), or executing tasks.
metadata:
  version: "0.1.0"
  category: "Cross-domain"
  subfield: "Research methodology and experiment design"
---

# discovery-work-breakdown

Turns a project's Purpose and Outcomes into a task tree that Discovery can
execute. Every task has validation requirements, every ordering is a real
dependency, risky or expensive steps sit behind a human pause, and compute
that won't fit on a workstation is planned up front rather than discovered
mid-run.

## Hard rules

1. **Never ask with the question modal.** Do not call `askQuestions`. Show
   the proposed shape in chat, ask any questions as a short numbered list,
   then end your turn and wait.
2. **Create nothing until the user approves.** Iterate on the shape as many
   times as they want first.
3. **Validation requirements on every task**: root, milestones, leaves, and
   pause tasks. The `tasks` tool takes them as `acceptanceCriteria` (an
   array of strings); Discovery stores them as `validationRequirements`.
4. **Dependencies go on the dependent task, never on a parent.** A parent
   that depends on its own children stays blocked forever. Order children
   with `addDependency` between siblings.
5. **Never delete, and never touch work in progress.** Leave tasks that are
   `complete`, `executionDone`, or `executing` alone. Retire unwanted tasks
   with `transition status=removed` plus a comment, and only when approved.
6. **Never invent thresholds.** If a rubric or outcome has no number, write
   `TBD` in the requirement and ask.

## Modes

- **Proposal-only** (called by `discovery-project-setup` Step 4): return
  `## Tasks` ops in that skill's proposal format and stop. Do not mutate.
- **Standalone**: gather, propose, approve, and apply, using
  `../discovery-project-setup/references/{gather-state,proposal-format,apply,git-ledger,retirement}.md`.

## Step 1. Gather

Read, don't guess:

| What | How |
|---|---|
| Purpose | `purpose_read get` |
| Outcomes | `purpose_read outcome.list` (skip titles starting `[Superseded]`) |
| Rubrics and latest grades | `purpose_read rubric.list`, `rubric.get`, `grade.latest` |
| Existing tasks, statuses, dependencies | `tasks_read graph`, `list`, `dependencyGraph` |
| Agents available | `agents list`, `.github/agents/` |
| Data and access gaps | `data/README.md`, `data/external-sources.md` (`Access: pending`) |
| Compute signals | Purpose, outcome evidence, and data sizes (see Step 5) |

If there is no Purpose or there are no Outcomes, stop and say so. Suggest
`discovery-define-outcomes`, or `discovery-project-setup`, first.

**Existing tasks.** Match each to an outcome by its `outcome.link-task`
link, then by title. Classify it as KEEP (fits as is), UPDATE (missing
validation, wrong parent, or missing dependency), or REMOVE (out of scope;
propose only, with a reason). Done or running work is always KEEP. New tasks
fill the gaps around it.

## Step 2. Shape the tree

```
Root: <project-level result>                         validation: every outcome passes
├── M1 <Outcome 1 as a result statement>            validation: Outcome 1 rubric passes
│   ├── L1.1 <work item>    [guided]                validation: concrete artifact checks
│   ├── L1.2 <work item>    [deterministic -> agent] depends: L1.1
│   ├── PAUSE P1.1 Human review: <what and why>     depends: L1.2
│   └── L1.3 <work item>    [autonomous]            depends: P1.1
└── M2 ...
```

- **Root**: one per project, titled as the overall result from the Purpose.
  Validation: each active outcome meets its rubric's passing criteria (name
  them).
- **Milestones**: one per active outcome, linked with `outcome.link-task`.
  Title each as a result, not an activity. Validation comes from the outcome
  (see below).
- **Leaves: at least two per milestone, with no upper limit.** Add leaves
  until each one is a single, checkable piece of work: one agent session or
  engine run that produces a named artifact or decision. Split a leaf that
  produces several unrelated artifacts, or that mixes a cheap step with an
  expensive one. Merge leaves too small to validate on their own. Leaves may
  depend on leaves in other milestones.
- Cover the whole path: data access and preparation, the analysis or
  experiment, checks, and the write-up the rubric grades.

### Validation requirements, by level

| Level | Source, in order of preference |
|---|---|
| Root | The passing criteria of each outcome's rubric |
| Milestone | The outcome's rubric: one requirement per question, phrased as a check, with the passing threshold (`minimumScore`, required yes/no answers). If there's no rubric, use the outcome's evidence criterion and flag the gap. |
| Leaf | Concrete and checkable: the artifact path or type, the quantity and threshold, the provenance to record. Each leaf requirement should feed a milestone requirement; say which. |
| Pause | What the human must confirm, and what they must record (decision, approver, date) in a task comment |

Use LaTeX for quantities (`$\le 5\%$`). Every requirement must be checkable
by someone other than its author.

## Step 3. Suggest an execution pattern per leaf

This is a suggestion, shown as a label, not a constraint. It borrows from
the [Microsoft Discovery advanced investigation patterns](https://learn.microsoft.com/en-us/azure/microsoft-discovery/concept-advanced-investigation-patterns):

| Pattern | Suggest when | In the task |
|---|---|---|
| `pattern:deterministic` | The steps and the right agent are known, and reproducibility matters | Name the agent and propose `assign` (type `Agent`); spell out the steps in the description |
| `pattern:guided` | The direction is known but the method is open | State the phase goal; leave the assignee empty so cognition picks |
| `pattern:autonomous` | Open-ended exploration where the approach isn't known | One clear prompt and strong validation; let an engine plan it |

Lean toward deterministic for anything that feeds a safety or cost pause, and
toward autonomous only when validation is strong enough to catch a wrong
answer. To design one milestone in detail as an investigation, offer
`discovery-design-tasks` afterwards.

## Step 4. Human-in-the-loop pauses

Insert a **pause task** immediately before any step that is risky or
expensive. Recommend one when a leaf would:

| Trigger | Label | Examples |
|---|---|---|
| Act on the physical world | `hitl:safety` | Send a protocol to an autonomous or cloud lab, run instruments, order or synthesize materials, handle hazardous or regulated substances |
| Spend significant money or quota | `hitl:cost` | HPC allocations, cloud GPU or large CPU jobs, paid APIs at volume, long engine runs (estimate with `token-estimator-mvp` if it's installed) |
| Touch sensitive data | `hitl:data` | PII, controlled or export-restricted data, sharing outside the project |
| Make an irreversible or external decision | `hitl:decision` | Publishing, submitting, deleting external resources, committing to one candidate |

A pause task is a real task:
- **Title**: `Human review: <what is being approved>`.
- **Assignee**: the user (`assignedTo: {type: User, id: <owner, or "project-owner">}`).
- **Labels**: `hitl-pause` plus the trigger label.
- **Description**: what to review, the risk or estimated cost, and the
  upstream artifacts to look at.
- **Validation**: the decision is recorded as a task comment with approver
  and date, plus the trigger's own check (safety review done, budget
  approved, data use approved).
- **Dependencies**: it depends on the work that produces what's being
  reviewed, and the risky step depends on it. Nothing downstream becomes
  ready until a human marks the pause `complete`.

Don't scatter pauses. Use one per real risk, at the last point before the
risk is incurred, and say in the proposal why each one is there.

## Step 5. Plan compute when the goals need it

Flag a leaf as compute-heavy (`compute:hpc` or `compute:cloud`) when the
work needs more than a workstation can do in reasonable time. Signals:
- molecular dynamics, DFT or other ab initio work, or CFD at scale
- training or fine-tuning models, or large ensembles and parameter sweeps
- genome-scale or large-cohort analysis
- datasets of hundreds of GB or more
- runs measured in GPU-days

For each one, replace the single "run it" leaf with this chain:

1. **Plan compute.** Estimate the job size (core-hours or GPU-hours, memory,
   storage, wall time), choose a target, and estimate the cost. Targets
   include Microsoft Discovery supercomputer and node-pool resources, other
   Azure compute (Azure Batch, CycleCloud, AKS GPU node pools), or an HPC
   allocation (Slurm or PBS; the genesis-skills `hpc-skills` cover DOE
   systems). Validation: the estimate, the chosen target and SKU, quota
   confirmed, and the cost range.
2. **Human review (`hitl:cost`).** Approve the budget and target.
3. **Provision.** Use infrastructure as code where possible, tag resources
   with the project, and reference credentials by env var or Key Vault name
   only. Use `discovery-provision-tools` for any MCP server or skill the job
   needs. Validation: the resources exist and a smoke job succeeds.
4. **Run.** The compute leaf itself. Validation includes actual resource use
   compared with the estimate.
5. **Tear down and report.** Release resources and record the actual cost.
   Validation: nothing is left running, and the cost is recorded.

Recommend compute only when an outcome actually needs it, and say which
outcome. If the need is uncertain, propose a small pilot leaf first and put
the scale-up behind the cost pause.

## Step 6. Present the shape and iterate

Show, in chat:
1. **Snapshot**: outcomes (and which have rubrics), existing tasks and their
   statuses, and what will be kept.
2. **The tree**, as in Step 2, with pattern labels, pauses, compute flags,
   and dependencies.
3. **Counts**: milestones, leaves per milestone, pauses by type, compute
   chains, and new, updated, kept, and removed tasks.
4. **Why**: one line for each pause and each compute chain.
5. **Gaps**: outcomes without rubrics, `TBD` thresholds, access-pending data.
6. **Questions**, as a short numbered list.

Then stop and wait. Accept the approval syntax from proposal-format.md
(`approve all`, `approve all except …`, `change M2: …`) or free-form
feedback; revise and re-show only what changed. Repeat until approved. In
Standalone mode, also write the proposal file.

## Step 7. Create the tasks

After approval, and after the git-ledger "before" commit:

1. Create the root, then the milestones (`parentId` = root), then the leaves
   and pause tasks (`parentId` = their milestone). Give each one a
   `description`, `acceptanceCriteria`, and `labels`: `root`, `milestone`,
   `leaf`, or `hitl-pause`, plus its pattern, trigger, and compute labels.
   Write the real IDs into the proposal as you go (`L1.2 -> DX-17`).
2. After the first create, `tasks_read get` that task and check that
   `validationRequirements` was stored. If it wasn't, stop and report.
3. `addDependency` for every edge: `taskId` is the dependent task,
   `dependsOnId` is the task it waits for.
4. `purpose outcome.link-task` for every milestone.
5. `assign` pause tasks to the user, and deterministic leaves to their named
   agents if those agents are installed. Leave the rest unassigned.
6. Apply the approved UPDATE and REMOVE ops on existing tasks.
7. **Verify** with `tasks_read graph` and `dependencyGraph`:
   - every new task exists under the right parent and has validation
     requirements
   - no parent depends on one of its own descendants
   - every pause sits between the work it reviews and the step it gates
   - `tasks_read ready` returns the expected first leaves
8. Make the git-ledger "after" commit, then report: created, updated,
   removed, and failed counts, the pauses, and the first ready tasks.

Failures follow apply.md: mark the op `FAILED`, continue with the ops that
don't depend on it, and never retry silently.
