# Outline heuristics (`propose_outline.py`)

The outline is a *proposal*. The user approves, edits or replaces it before anything is written.

1. **Units.** Each task-tree child of a root task is one unit, and every sub-task maps to it. A root task with children is an umbrella: it maps to no unit, and its events are placed by time. The purpose, and any outcomes created before the first task, form the `SETUP` unit. Later outcomes are scope events, placed by time with a strong signal.
2. **Events.** Human turns, form answers, commits, task creation, engine runs, dispatches (subagents, Clio, agent runs), grades and outcomes. An event with no task reference joins the next mapped event within 6 hours, otherwise the previous one, and is flagged `inferredUnit`. The count of such events is reported under gaps.
3. **Signal.**
   - Human turns score higher when they redirect ("instead", "stop", "wrong", "not what I"), set a gate ("approve", "must", "before"), or are form answers. Short acknowledgements ("ok", "yes") score low.
   - Reopened tasks, failed grades and gate commits score high.
4. **Unit score.** This combines concurrency (overlapping runs), the variety of actor types, strong human turns, and gates.
5. **Scene count.**
   - The budget is round((spoken seconds − 60) / 135), clamped to 3–12.
   - The target is min(budget, number of units scoring 6 or more), with a floor of 3.
   - Adjacent units are merged at minimum cost: a same-root merge is cheaper, and a merge across a long time gap costs more. Weak units are merged rather than dropped; they are listed under `lowSignalUnitsMerged`.
   - Compact (−2) and detailed (+3) alternatives are always reported.
6. **Seconds.** Each scene gets time in proportion to √score, clamped to 60–240 s and rounded to 15 s. The token scene gets 60 s, or 0 s when it is in the appendix.
7. **Beats.**
   - k = scene seconds / 32, clamped to 2–9.
   - Candidates are human turns, gates, scope events, dispatch waves (runs starting within 20 minutes of each other) and artifacts.
   - Human beats within 3 minutes of each other are de-duplicated.
   - Each beat carries `refs` for the evidence behind it. A non-human beat also carries `candidateCause`, the nearest prior human turn, which is a hint only.
8. **Mode.** `flow` when the maximum concurrency is 2 or more, or there are at least 3 actor types. `metrics` for the token scene. `tree` otherwise.
9. **Flags.**
   - `loop`: the evidence mentions a cycle, round or iteration. Consider a tree with `loop` routes, or a flow with a back-edge.
   - `metaCandidate`: the scene is about the presentation or story itself. Usually cut it; ask the user.

When the heuristic is wrong (a scene that mixes two ideas, or two scenes that are one idea), fix `outline.json` by hand before asking for approval, and explain the change in the proposal you show the user.
