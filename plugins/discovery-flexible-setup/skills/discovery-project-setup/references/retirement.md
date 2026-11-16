# Retirement conventions (never delete)

| Artifact | How to retire | Notes |
|---|---|---|
| Task | `tasks transition status=removed` with `comment: "Removed by <proposal-id>: <reason>"` | Children: retire each one explicitly in the proposal; never cascade silently. |
| Outcome | **Superseded convention** (below) | Keep its grades and rubrics untouched. |
| Rubric | Leave it attached to the superseded outcome | |
| Shelf | Rename to `[Retired] <name>` if the tool supports rename; otherwise record in `setup.json` → `retired[]` | Never delete documents or indices. |
| Dataset in data/ | Move the manifest entry to a `## Retired` section in `data/README.md`; keep the file | |
| External source | Move to `## Retired` in `data/external-sources.md` | |
| Skill / agent / MCP | Disable rather than remove: comment out the MCP entry, or move to `.github/_retired/` | Ask before moving. |

## Superseded outcome (convention A: no platform status exists yet)

The `purpose` outcome statuses are Pending, InProgress, Achieved,
PartiallyAchieved and NotAchieved. None of them means "superseded".
Until the platform adds one:

1. `outcome.update` title -> `[Superseded] <original title>`.
2. Prepend to the description:
   `Superseded by <newOutcomeId> on <YYYY-MM-DD> (<proposal-id>): <reason>.`
3. Leave the status unchanged. Don't fake it with NotAchieved.
4. Re-link its tasks to the replacement (`outcome.unlink-task` and
   `outcome.link-task`), listing each re-link as an op.
5. Record it in `setup.json` → `superseded[]`.

When the platform adds a real status, change only this section.

Counting rule: anything that counts outcomes (the 4–10 guidance, progress
summaries) excludes titles starting with `[Superseded]`.
