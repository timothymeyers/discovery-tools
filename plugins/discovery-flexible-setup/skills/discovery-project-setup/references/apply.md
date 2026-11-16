# Apply an approved proposal

1. **Pre-commit**: see git-ledger.md (before).
2. **Order**: Purpose, then Shelves, then Ingest/Data, then Outcomes (and rubrics),
   then Tasks (root, milestones, leaves), then links and dependencies, then
   Step 5 installs, then assignments. Within an area, follow the op dependencies.
3. **Execute only `[x]` ops.** Map local IDs to real IDs as you go and write
   them into the proposal (`T3 -> DX-17`).
4. **Tool mapping**:
   - Purpose: `purpose set` / `update`.
   - Outcomes: `purpose outcome.create` / `outcome.update`, then `rubric.generate`.
     Use `rubric.create` when the proposal includes explicit questions.
   - Links: `purpose outcome.link-task`.
   - Tasks: `tasks create` (with `parentId`, `acceptanceCriteria`, `labels`),
     `addDependency`, `transition status=removed` (with a comment), and `assign`.
   - Bookshelf: `bookshelf` create, then ingest.
   - Data: copy files, then update `data/README.md` and `data/external-sources.md`.
5. **Failures**: don't stop silently. Mark the op `FAILED: <error>` in
   the proposal, continue with the ops that don't depend on it, and skip
   the dependent ops as `SKIPPED (dep failed)`.
6. **Verify**: re-run gather-state and check that each applied op is
   reflected (purpose title matches, tasks exist under the right parent,
   ingest status is queued or complete, and the MCP server lists its tools).
7. Set the proposal `Status: applied | partial`, update `setup.json` step
   status, then **post-commit** (git-ledger.md).
8. Report: applied, failed and skipped counts, plus any follow-up needed.
