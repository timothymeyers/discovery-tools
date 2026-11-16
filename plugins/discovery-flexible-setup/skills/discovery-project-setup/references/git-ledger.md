# Git ledger

Each apply is bracketed by two commits. **Never push**: pushing is out of
scope for now.

## Preconditions
- If the folder isn't a repo, propose `git init` as an op. Don't run it unasked.
- If the branch has no commits, the "before" commit doubles as the
  initial commit. Say so in the message.
- Ensure `.gitignore` contains the policy below. Add any missing lines as
  a proposal op the first time.
- **Unrelated uncommitted changes:** don't sweep them into ledger commits.
  Stage only setup-owned paths (listed below). If setup-owned paths have
  edits the user made outside this workflow, show them and ask before committing.

## Setup-owned paths (staged by ledger commits)
```
.discovery/purpose.json  .discovery/outcomes/  .discovery/rubrics/
.discovery/tasks/  .discovery/bookshelf/  .discovery/setup/
.discovery/config.json  .vscode/mcp.json  .github/agents/  .github/skills/
data/  .gitignore
```

## .gitignore policy
All Discovery exhaust is **tracked**: `logs/`, `runtime/`, `sessions/`,
`chat-sessions/`, `copilot-chat/`, `engine/`, `engine-runs/`, `agent-runs/`,
`science-runs/`, `grades/`, and telemetry. Never add those to `.gitignore`.
Ignore only:
```
# Bookshelf search indices (rebuildable, large)
.discovery/bookshelf/providers/
# Tool catalog cache (rebuilt by the app)
.discovery/toolcatalog/
# Secrets
.env
.env.*
!.env.example
.DS_Store
```
If an existing `.gitignore` ignores any exhaust path, propose removing that
line as an op.

## Exhaust secret check (before every commit that includes exhaust)
Run `git diff --cached` over the exhaust paths and search for
`authorization`, `bearer `, `api[-_]?key`, `secret`, `password`, `sk-`,
`ghp_`, `eyJ` (JWTs). Matches are fine if the value reads `[redacted]` or
the endpoint is `127.0.0.1` loopback. Any other hit stops the commit:
show the files and lines to the user and don't commit until they decide.
Re-check whenever a remote LLM provider or remote endpoint is configured.

## Exhaust commits
Exhaust isn't setup-owned, so ledger commits don't include it. When
`git status` shows exhaust changes before an apply, propose one separate
commit first: `chore(exhaust): snapshot Discovery exhaust`, after the
secret check passes.
`.discovery/bookshelf/<shelfId>/extracted_content` stays tracked. It's
the normalized text of ingested sources, it's small, and it diffs well.
If any shelf's extracted content exceeds 50 MB, warn and offer to ignore it.

## Commits
Before apply:
```
setup(<proposal-id>): snapshot before apply

Mode: <mode>. Approved ops: <n>/<total>.
```
After apply:
```
setup(<proposal-id>): applied <n> ops (<f> failed, <s> skipped)

Purpose: <changed|unchanged>; outcomes +a ~u -s; tasks +a ~u removed r;
shelves +a; data +a; tools +a.
Approved-by: <user reply verbatim, truncated to 72 chars>
```
If there is nothing to commit, skip it silently. Never use `--no-verify`
or amend earlier commits.
