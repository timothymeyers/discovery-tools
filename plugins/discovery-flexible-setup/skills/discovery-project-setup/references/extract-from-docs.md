# Extract a project draft from documents

Applies to grant applications, project proposals, SOWs, papers, and slide
decks supplied in Step 1 or 2.

## Read
- PDF / DOCX / XLSX: use the `convert-pdf-to-md`, `convert-word-to-md`,
  `convert-excel-to-md` skills. Do not hand-parse binaries.
- Stage the original into `.discovery/setup/staged/` (copy, don't move) and
  record it in `setup.json` → `staged[]` with sha256 and intended shelf.

## Extract (map document sections → project fields)

| Look for | Becomes |
|---|---|
| Title, project summary, abstract | Purpose title + statement |
| Specific aims, objectives, research questions | Outcome candidates (Knowledge / Decision) |
| Deliverables, milestones, products | Outcome candidates (Artifact / Capability) |
| Evaluation plan, metrics, success criteria | Outcome evidence criteria, rubric questions |
| Work plan, timeline, phases | Task milestones and dependencies |
| Data management plan, datasets, facilities | Data intake entries |
| Collaborators, external systems, compute | External sources, A2A/tool candidates |
| Risks, assumptions | Clarify-pass questions |

## Rules
- Every drafted field cites its source: `[grant.pdf §2.1]`.
- Don't invent metrics. If the document gives none for an outcome, mark
  the criterion `TBD — ask user` and include it in the questions.
- Rephrase grant language into an operational statement: what will be
  determined, under what conditions, measured how.
- Note scope that looks out of reach for a Discovery project, such as
  wet-lab work or hiring, as "out of scope for this workspace", not as tasks.
