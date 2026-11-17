# Narrative rules

## Voice
- The audience has not seen the project. Write plain language: no internal vocabulary, task ids, agent ids or file names on the main surface. Rename agents by what they do ("statistics checker", not `catalog/stat-agent`), both in graph labels and in prose.
- Each beat answers three questions: what the human asked or decided, what Discovery did, and what exists now because of it.
- Present tense for the replay, with short sentences.
  - Summary: 1–2 sentences, at most about 180 characters.
  - Action and result: one sentence each.
  - Card labels: at most about 60 characters.

## Truth
- **Quotes** are exact and come only from `evidence.json`. Excerpts are exact substrings. Never fix typos in a quote.
- **Summaries** paraphrase the quote and nothing more. Do not add intent the human did not state.
- **Causation:** link a human turn to a later result only if the record connects them: the same request, a task the turn created, or a commit that cites it. `candidateCause` is a hint for you to check, not a claim you can make.
- **Counts** (runs, reviewers, rounds, tokens) must match `evidence.json`. When the numbers are approximate, say "about".
- **Review:** "simulated AI review", "AI reviewers" — never "peer review" or "referees" without the qualifier.
- **Validation:** checks in software or simulation are not clinical, field or user validation.
- **Approval and rehearsal:** never claim the user approved, rehearsed or presented something unless the record shows it. The story's own approval appears only as the verbatim lock text in "About".
- **Failures and reversals stay in.** A reopened task, a failed grade or a correction is often the most informative beat. Show the task as reopened; don't move that state onto an artifact.
- **Gaps:** if the evidence is thin (no chat, no Clio tokens, missing runs), say so in `about.caveats` and in the presenter notes rather than filling the gap.

## Scene shape
- One idea per scene. The title states the idea: "Three reviewers disagree, then converge", not "Review loop".
- Choose the mode that fits the idea:
  - **tree** for a sequence of decisions;
  - **flow** when who did what, and in parallel, matters;
  - **metrics** for quantities.
- Put the first human beat first when the scene starts with a human decision.
- Presenter notes should say what to stress, how long to spend, and one thing *not* to claim.

## Worker contract (parallel scene writers)
Give each subagent exactly one scene. Paste in:
1. the scene's locked entry;
2. its `story.json` scene object;
3. the `evidence.json` records named in each beat's `evidenceRefs`, plus the quoted human turns;
4. this file, and the schema sections for the scene's mode.

The worker must:
- Return only the completed scene object as JSON: the same `id`, `mode` and beat ids, and the same `human.quoteId` values.
- Not change `evidenceRefs`, `chronology` or `exchangeIds`.
- Write no text on the main surface that it cannot trace to the supplied records.
- List any claim it was unsure of in `notes`, prefixed with `CHECK:`. The lead resolves or removes these before the build.

The lead then:
- merges the scene objects;
- keeps the terminology consistent across scenes (the same agent names, the same titles for recurring artifacts);
- runs `build --only` for each scene, then the full build.

## Build lint (warnings)
- "peer review" without "AI" or "simulated"
- "clinically validated"
- "the user approved"
- "proves" or "guarantees"

The lint is negation-aware ("not clinical validation" passes).

## Regression list (check each one in QA screenshots)
These defects happened in earlier hand-built versions:
1. Dispatches bound to an actor by list position instead of run identity. Nodes and counts must come from `evidenceRefs` runs.
2. A reopened state shown on an artifact card instead of on the task.
3. An artifact button that promised a preview but showed only a title. Omit `artifact` when there is no content.
4. Header or title text clipped at 1280×720.
5. An SVG `hidden` property that had no effect. The renderer uses attributes and `visibility`, and QA checks that no dots are visible when paused or in reduced motion.
6. A scene-specific special case leaking into other scenes. Keep scene data declarative; never branch the template on a scene id.
7. A `primaryId: null` beat that fell back to an earlier quote. It must show `Project scope` or `Recorded outcome` and no quote.
8. Playwright timing in a background tab. `qa.cjs` calls `bringToFront()` before any timing check.
9. Task, run or quote ids on the main surface. Enforced at build and in QA.
10. Play scene running on into the next scene. It must stop at the end of the scene.
