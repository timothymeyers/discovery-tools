---
name: discovery-define-outcomes
description: |
  Cross-domain / Research methodology — Draft, tighten, add, or supersede the
  measurable Outcomes (and draft rubrics) of a Microsoft Discovery project.
  Outcomes are derived from the Purpose, project documents, and user goals;
  each gets a type, evidence criterion, source citation, and rubric. Never
  deletes: replaced outcomes are marked superseded. USE FOR: 'define
  outcomes', 'what are my success criteria', 'tighten my outcomes', 'add an
  outcome', 'these outcomes are vague', 'replace outcome X', 'generate
  rubrics for my outcomes'. DO NOT USE FOR: grading outcomes (use the
  purpose tool grade.*), full project setup (use discovery-project-setup),
  or task planning (use discovery-design-tasks).
metadata:
  version: "0.1.0"
  category: "Cross-domain"
  subfield: "Research methodology and experiment design"
---

# discovery-define-outcomes

## Modes
- **Proposal-only** (called by `discovery-project-setup`): return ops in
  that skill's proposal format and stop. Do not mutate.
- **Standalone**: gather, propose, approve, apply using
  `../discovery-project-setup/references/{gather-state,proposal-format,apply,git-ledger,retirement}.md`.
  Ask questions as plain chat, not the question modal.

## Inputs
Purpose (`purpose_read get`), current outcomes (`outcome.list`), staged or
ingested documents, and the user's stated goals.

## Drafting rules
- **Count:** default 4–5. Go up to 10 only when the material clearly
  supports distinct, separately verifiable results. Exclude `[Superseded]`
  outcomes from the count. Over 10 means split the project or merge outcomes;
  say which.
- **Type**, one of: `Artifact` (a thing produced), `Measurement` (a
  quantified result), `Decision` (a choice made with evidence), `Knowledge`
  (a finding or answer), `Capability` (something that now works).
- **Each outcome has:**
  - a title: a short result statement, not an activity ("Validated N-1
    screen on synthetic grid", not "Run N-1 screen")
  - evidence: what artifact or measurement shows it's done, with a threshold
    when one exists (use LaTeX for quantities, e.g. $\le 5\%$)
  - a source citation: the document section or the user's words
  - a rubric draft: 2–4 questions, `yesNo` or `scale`, and passing criteria
- **Quality checks**, applied before proposing. Flag outcomes that fail them:
  - Verifiable by someone other than the author?
  - Distinct from the other outcomes, with no overlap?
  - Achievable inside this workspace (no wet-lab, procurement, or hiring)?
  - Does the evidence name a concrete artifact or number?
- Missing thresholds: write `TBD` and add a question rather than inventing one.

## Update mode
Compare the new information with the current outcomes and emit `KEEP`, `UPDATE`
(show before -> after), `CREATE`, or `SUPERSEDE` (per retirement.md, including
task re-links). Every non-KEEP op quotes the new information that motivates it.

## Output
Proposal ops under `## Outcomes`. Rubrics are applied after their outcome
exists: `rubric.create` when the proposal includes questions, otherwise
`rubric.generate`.
