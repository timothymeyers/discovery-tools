# story.json schema (`discovery-project-story/story@1`)

Produced by `build_story.py scaffold`, completed by the author, then compiled by `build`. The fields marked **auto** are filled in by the scaffold or the build; leave them alone unless noted.

## Top level
| field | notes |
|---|---|
| `title`, `subtitle` | Shown in the header. Plain language. |
| `audience`, `minutes` | From the outline and lock. |
| `gapMs` (1000), `phaseMs` (1400) | Pause between beats during Play scene; duration of one animation phase. |
| `theme.accentLight`, `theme.accentDark` | Must be `#rgb` or `#rrggbb`. |
| `about.method`, `about.caveats[]` | "About this story" drawer. The caveats are auto-seeded from outline gaps and token caveats; keep the honest ones. |
| `scenes[]` | Must match the lock: same ids, modes and beat ids, in the same order. |

## Scene
| field | notes |
|---|---|
| `id`, `mode`, `seconds`, `appendix` | Taken from the lock (**auto**). |
| `title` | The approved title. Changing it gives a warning; confirm the change with the user. |
| `milestone` | Optional short tag in the eyebrow line (e.g. "Week 1"). |
| `notes` | Presenter notes (drawer). Say what to stress and what not to claim. |
| `chronology[]` | **auto**: `{t, kind, label, ref}`, shown in the Chronology drawer. May contain ids. |
| `tree` | tree mode: `{root, branches[{title, sub}]}`, 1–6 branches. |
| `graph` | flow mode: `{nodes[], edges[]}` (**auto** draft; edit it freely). |
| `metrics` | metrics mode: `{groups[{id, label, rows[{label, input, output, cachedRead, calls}]}], caveats[]}` (**auto** from tokens). |

## Beat
| field | notes |
|---|---|
| `id` | From the lock. |
| `label` | Short beat title (footer and beat buttons), 3–8 words. |
| `human.quoteId` | Exact quote id (`H###`), or `null`. It must equal the locked `primaryId`. |
| `human.excerpt` | Optional **exact substring** of the quote to display. Use it to skip ids or boilerplate. The full text stays in the exchange drawer. |
| `human.heading` | Only when `quoteId` is null: `"Project scope"` or `"Recorded outcome"`. |
| `human.summary` | Plain-language summary, labelled "not a quote" when a quote is shown. |
| `human.relation` | Optional: how this human turn relates to the beat, *as the record shows*. |
| `exchangeIds[]` | **auto**: the quote plus the form answers from the same request (exchange drawer). |
| `discovery.action`, `discovery.result` | What Discovery did, and what came of it. |
| `artifact` | `{label, preview, detail, source}`. `preview` is plain language on the main surface; `detail` and `source` are drawer-only and may hold the commit body and hash. Omit the artifact if there is no real record. |
| `notes` | Presenter note for this beat. |
| `evidenceRefs` | **auto**: commits, tasks, runs, grades, `candidateCause` and `commitCandidates`. Stripped from the build. Read them before writing. |
| `tree` / `flow` / `metrics` | Mode-specific; see below. |

## Tree mode geometry (viewBox 1440×780)
- The root column sits at x 20–185. Branches are 275 px wide at x 260, laid out vertically by `branchLayout(n)`. Past branches get a check mark; the active branch has an accent border and a connector to the heading.
- `beat.tree = {branch, title, cards[], routes[], phases[][], note}`
  - `branch`: index of the active branch.
  - `cards[{id, role: human|discovery|record, label, col: 0|0.5|1, row: 0|1|2}]`: 330×106 cards at x = 580 + col·420 and y = 140/350/560. A card at col 0.5 must be alone in its row. Labels fit two lines (24→14 px). Keep them to about 60 characters.
  - `routes[{from, to, label?, loop?}]`: same row → straight horizontal line; same column → vertical; otherwise an S-curve. Set `loop: true` for a return path, drawn dashed under the cards.
  - `phases`: a list of route-index lists. Every route appears exactly once. Routes in the same phase animate together.
- Use a HUMAN card only for a recorded human action. Use a RECORD card only for something that exists in the record.

## Flow mode geometry
- There are 4 lanes, centred at x 150/530/910/1290, with nodes 250 px wide. The conventional lanes are:
  - 0: human
  - 1: Discovery chat, engines, task board
  - 2: agents
  - 3: records and files
- Up to 7 nodes per lane. Group repeated runs into one node with `count` (drawn as a stack with a "×N" badge).
- `node = {id, label, kind: human|discovery|agent|record, lane, count?, kindLabel?}`; `edge = {id, from, to, label?}`.
- `beat.flow = {title, active[], phases[[{edge, count?, reverse?}]], note}`
  - `count` on a phase draws that many parallel dots (at most 5, then "×N"), offset side by side. Parallel runs move together and are not staggered.
  - **auto** at build time: `visitedNodes` and `visitedEdges` from earlier beats.
- Avoid edges that skip a lane when nodes sit in the lane between; QA warns if a route crosses a box that is not one of its endpoints.

## Metrics mode
- `beat.metrics = {group, highlight[], title, note}`. `group` names a `scene.metrics.groups[].id`; the `highlight` rows stay bright while the others dim.
- Input bars start at x 330 (500 px at maximum, with the cached share drawn lighter). Output bars start at x 1050 (230 px at maximum). Up to 12 rows. The bars grow during the beat.
- Always keep the caveats. Never present missing Clio or engine usage as zero work.

## Build-computed (do not author)
`animationMs`, `phaseWindows`, `routePhase`, `visitedNodes`, `visitedEdges`, `beatCount`, `quotes{}` (exact text, date and a source locator without a path), and `build{storySha256, lockSha256, templateSha256, sourceHead, approval}`.

## Renderer contract
- A single RAF owner, cancelled by generation. `jump` lands on the settled end state. Play beat plays one beat. Play scene plays through the beats with a `gapMs` pause and stops at the end of the scene.
- Reduced motion shows the end state with no dots.
- All text is written with `textContent`. The page has a strict CSP and makes no network requests.
- QA hooks: `window.STORY = {data, jump(s,b,t?), seek(ms), stop(), playBeat(), playScene(), state()}`.
