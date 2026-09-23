---
name: discovery-project-story
description: |
  Cross-domain / Research communication — Turn a Microsoft Discovery project's
  own record (git history, task board, engine/agent/Clio runs, outcomes and
  grades, the human side of chat sessions, token usage) into an evidence-true,
  animated, self-contained HTML presentation of how the work happened. The
  number of scenes and beats comes from the evidence and needs the user's
  approval before any prose is written. Each scene renders as a tree, a flow
  graph or a metrics chart, and every human quote is exact and re-verified at
  build time. WHEN: "tell the story of this project", "what happened in this
  project", "present how Discovery did this work", "project retrospective
  deck", "replay the project", "animated walkthrough of the work", "how was
  this built with Discovery", "project story HTML", "explain the project to
  leadership", "demo what Discovery did". DO NOT USE FOR: writing the research
  paper itself (arxiv-paper), PowerPoint decks (pptx), or token-only read-outs
  (discovery-token-usage).
metadata:
  version: "1"
  category: "Cross-domain"
  subfield: "Research communication and storytelling"
  secondary: "Research platform observability (reads the same .discovery exhaust)"
---

# Discovery project story

Builds `discovery-work.html`, a single offline file that replays how a Discovery project unfolded. It uses real human words and real records, shown as plain-language scenes with animated tree, flow or metrics diagrams. Presenter notes, the exact exchange, the chronology and the record behind each beat sit one click away.

Everything lives in this skill folder. Only the Python standard library is needed; QA needs Node with `playwright-core` and Chromium. Nothing in the target workspace is modified until the user chooses where to save the release.

```
scripts/mine_exhaust.py     workspace → evidence.json (read-only mining)
scripts/verify_quotes.py    re-read every quoted human turn from its source file
scripts/propose_outline.py  evidence → outline.json + outline-proposal.md (scene count from evidence)
scripts/lock_outline.py     freeze the approved outline (hash + the user's verbatim approval)
scripts/build_story.py      scaffold story.json · build + validate → discovery-work.html
scripts/qa.cjs              browser QA: themes × viewports × every beat, playback, drawers
templates/discovery-work.template.html   self-contained renderer (own --ps-* theme)
references/*.md              schema, sources, heuristics, narrative rules, known defects
```

## Dependencies

The mining, outline, lock and build scripts are **Python standard library only**.

`scripts/qa.cjs` needs **Node** plus **`playwright-core`** and a **Chromium**
build. It resolves them in this order, so usually nothing needs installing:

1. `$PLAYWRIGHT_CORE` / `$PLAYWRIGHT_CHROMIUM` if set;
2. the `playwright-core` bundled inside Discovery App - Preview (macOS);
3. a `playwright-core` resolvable from `NODE_PATH`;
4. the newest `chromium-*` under `~/Library/Caches/ms-playwright`.

If QA reports `playwright-core not found`, either set `PLAYWRIGHT_CORE` to a
checkout or run `npm i playwright-core && npx playwright install chromium`.
Steps 2 and 4 are macOS paths; on Linux or Windows set the environment
variables. Everything except step 8 (QA) works without Node.

## Locate the payload — once per session

This skill may be installed as a personal skill, a project skill, or as part of
a plugin, so never hardcode its location. Resolve it once and reuse `$SK`:

```bash
SK=$(dirname "$(find ~/.copilot/skills ~/.copilot/installed-plugins ~/.agents/skills \
                    .github/skills .claude/skills .agents/skills \
                    -name mine_exhaust.py -path '*discovery-project-story*' \
                    2>/dev/null | head -1)")
echo "$SK"
```

If `$SK` is empty, tell the user the skill payload could not be located and
stop. Then set `WORK=<scratch dir>` — use something like
`/private/tmp/story-<project>`, never the repository, until the user picks a
release location.

## Hard rules

1. **The user approves the shape.** Never write scene prose before the outline is approved and locked. If the story needs a different shape later, re-propose, re-ask and re-lock; the build refuses to run on a drifted outline.
2. **Exact words or no quotes.** Human quotes come only from `evidence.json`; they are verified against the source line and sha256 on every build. Summaries are labelled "In plain words — not a quote". A beat with no human quote uses the heading `Project scope` or `Recorded outcome`, never a borrowed quote. Assistant text is never quoted.
3. **Say only what the record shows.** A time-adjacent human turn is not proof that it caused something (`candidateCause` is a hint, not a fact). AI review is "simulated AI review", never peer review. Software checks are not clinical or field validation. Never claim the user rehearsed, approved or presented anything unless the record says so. The rehearsal belongs to the user.
4. **No internals on the main surface.** No task ids, `H###` ids, hashes, paths or file names on the main surface. They belong in drawers only; the build and QA both enforce this. If an exact quote contains an id, show an exact `human.excerpt` that leaves it out.
5. **Chat logs are opt-in per run.** Ask before mining chat sessions. Exclude the session you are running in (its id is the final path segment of the chat debug-log directory, if known) so the story does not narrate its own making.
6. **Discovery App terminal:** prefix commands with `clear;`, do not use heredocs, and write helper scripts with the file tool.

## Workflow

### 1. Preflight (ask once, with a question form; every question needs 2 or more options)
- Workspace path (default: current) and range: whole history, a branch, or `--since <rev>`.
- Audience (for example leadership, scientists or engineers) and spoken length in minutes (default 15).
- Chat sessions: include (recommended) or exclude.
- Token scene: include as the final scene (default), put it in the appendix, or leave it out.

### 2. Mine
```
python3 $SK/mine_exhaust.py <WS> --out $WORK [--since REV] [--no-chat] [--exclude-session <id>]
python3 $SK/verify_quotes.py $WORK/evidence.json        # must be all PASS
```
Read `evidence.json` → `notes` and `tokens.caveats`. Missing sources degrade to empty lists; report them to the user and do not invent content. If **no human turns** were found, the chats may live in another app's storage (pass `--chat-root <…/workspaceStorage>`), or the work may have been driven entirely by engines. Copilot CLI `session-state` sessions launched by Discovery hold engine prompts, not human words, so do not quote them. Ask the user whether to point at the chat storage or to accept a records-only story, which uses `Project scope` / `Recorded outcome` beats.

### 3. Propose
```
python3 $SK/propose_outline.py $WORK/evidence.json --out $WORK --minutes <m> --audience "<a>" [--token-appendix|--no-token-scene]
```
The scene count follows the evidence. The budget is (spoken seconds − 60) / 135, clamped to 3–12; the proposal uses fewer scenes when fewer units carry a strong signal, and never pads. See `references/outline-heuristics.md`. Read `outline-proposal.md` in full. Then, before showing it, edit `outline.json`:
- Give each scene a plain-language `title` (short, active, understandable without the project's vocabulary).
- Check the mode for each scene:
  - `tree` for a sequence of decisions or steps;
  - `flow` when several actors or parallel runs matter;
  - `metrics` for quantities.
- Flag `metaCandidate` scenes: work on the presentation itself is usually cut or merged, but the user decides.
- Drop, merge or split beats only where the evidence supports it; keep `primaryId` values as proposed unless swapping in another exact quote from the same scene.

### 4. Ask for approval
Show the table (scene, title, mode, seconds, beats and why) along with the alternatives and gaps. Ask with a form, for example:
- Approve as is.
- Use the compact version.
- Use the detailed version.
- Change specific scenes (free text).

Iterate until the user approves. If the user asks for a specific count, re-run with `--scenes N`.

### 5. Lock
```
python3 $SK/lock_outline.py $WORK/outline.json --approval "<the user's exact approving words>"
```

### 6. Scaffold, then write
```
python3 $SK/build_story.py scaffold --dir $WORK
```
`story.json` now contains every approved scene and beat, with the exact quote ids, candidate artifacts (`evidenceRefs.commitCandidates`), the chronology, auto-built flow graphs, token metrics, and `TODO` in every field you must write. Follow `references/narrative-rules.md` and `references/story-schema.md`. For each beat, read its `evidenceRefs` (commits, tasks, runs and grades in `evidence.json`) before writing; the prose must be traceable to them. When there are 4 or more scenes, you may write them in parallel with subagents, one scene each, using the worker contract in `references/narrative-rules.md`, then merge the results yourself.

Iterate on a single scene with a preview build:
```
python3 $SK/build_story.py build --dir $WORK --only S3        # → $WORK/preview-S3.html
```

### 7. Build + verify
```
python3 $SK/build_story.py build --dir $WORK --out $WORK/release
```
The build fails on any of the following:
- a remaining TODO;
- lock drift;
- an unknown or changed quote;
- an id on the main surface;
- a borrowed quote;
- geometry out of range.

It warns on narrative lint (for example "peer review" or "clinically validated"). Fix the warnings or justify them to the user. The output is deterministic: identical inputs give identical bytes.

### 8. QA
```
node $SK/qa.cjs $WORK/release/discovery-work.html            # --quick for 1600×1000 only
```
The run must end with 0 errors. Open `release/qa/contact-sheet.html` and look at the screenshots yourself (use the image viewer): check the tree, flow and metrics scenes, the dark theme and the 390 px width. Fix the causes in `story.json`, not by editing the HTML.

### 9. Release
Ask where to save it (for example `presentation/<id>/` in the workspace). Copy `discovery-work.html`, `build-report.json`, `story.json` and `outline.lock.json`. Add a short presenter README covering:
- the controls (← →, PageUp and PageDown, Space, P, N, E, `#s=&b=`, `?theme=`);
- the planned seconds per scene;
- the caveats.

Do not commit or push unless asked. Report the sha256, the number of scenes and beats, the quotes verified, the QA result, and what the user should rehearse.

## Reference
- `references/exhaust-sources.md`: where each kind of evidence comes from, its formats and known traps.
- `references/outline-heuristics.md`: how units, scores, the scene count, beats and modes are derived.
- `references/story-schema.md`: `story.json` fields and layout geometry for tree, flow and metrics.
- `references/narrative-rules.md`: writing rules, the worker contract, lint, and the regression list to check.
