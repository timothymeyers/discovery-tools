---
name: discovery-provision-tools
description: |
  Cross-domain / Research platform engineering — Recommend and install the
  agents, skills, MCP servers, and remote (A2A) agents a Discovery project
  needs. Accepts local paths, URLs, GitHub repos, MCP server commands/URLs,
  and A2A agent endpoints; reviews the microsoft/discovery and
  timothymeyers/discovery-tools catalogs plus any extra catalog repos the
  user names; ties every recommendation to a task or data-access gap; shows
  a trust review before installing anything. USE FOR: 'what tools should I
  load', 'install this skill', 'add this MCP server', 'hook up this
  remote agent', 'connect to this A2A agent', 'find agents for my
  project', 'review the discovery catalogs', 'close the access gap for this
  database'. DO NOT USE FOR: deploying agents to Azure/Foundry (use
  discovery-add-agents directly), running agents, or full project setup
  (use discovery-project-setup).
metadata:
  version: "0.1.0"
  category: "Cross-domain"
  subfield: "Research platform engineering"
---

# discovery-provision-tools

## Modes
- **Proposal-only** (called by `discovery-project-setup` Step 5): return
  `## Tools / agents / skills` ops and stop.
- **Standalone**: gather, propose, approve, apply using
  `../discovery-project-setup/references/{gather-state,proposal-format,apply,git-ledger,retirement}.md`.
  Ask questions as plain chat.

## 1. Collect needs
Read the tasks (especially `access-pending` labels), outcomes, and
`data/external-sources.md`. Build a needs list: "T7 needs read access to
ERCOT historian", "O2 needs AC power-flow solver".

Then ask (plain chat):
> Do you have agents, skills, or tools to bring in — local paths, URLs,
> GitHub repos, MCP servers, or remote (A2A) agents running elsewhere?

## 2. Identify user-supplied sources
See [references/source-types.md](references/source-types.md) for detection
and the recommended install for each type. For A2A agents see
[references/a2a.md](references/a2a.md).

## 3. Catalog review (run in parallel)
Run one subagent per catalog. Each returns at most 10 candidates, each with a
name, kind, path, commit SHA, a one-line purpose, and the need it serves.

| Catalog | Visibility | How |
|---|---|---|
| `microsoft/discovery` | public | Read `../discovery-add-agents/SKILL.md` and use its catalog listing in recommend-only mode (no deploy). |
| `timothymeyers/discovery-tools` | public | `gh api repos/timothymeyers/discovery-tools/git/trees/HEAD?recursive=1`, then read the candidate manifests (`SKILL.md`, `*.agent.md`, `agent.yaml`, MCP `package.json` / `pyproject.toml`). |
| Extra catalogs the user names (`<owner>/<repo>`) | public or private | Ask once whether there are other catalog repos to review. For each, check `gh auth status`, then do the same as above. On 404 or 403, continue without it and report: "`<owner>/<repo>` not reviewed — run `gh auth login` with an account that can read it". Never fail silently. |

Use `gh api` for every catalog. Cache results in
`.discovery/setup/catalog-cache/<owner>__<repo>.json`:
`{sha, fetchedAt, candidates:[{name,kind,path,purpose}]}`. For a private
repo, store names, paths and SHA only, no file contents. Re-runs fetch HEAD
and only re-review when the SHA has changed.

Merge the results, de-duplicate across catalogs (prefer the one the user
supplied, then extra catalogs the user named, then discovery-tools, then
microsoft/discovery, unless versions differ), and rank by how many needs each candidate covers.
Drop candidates that cover no need, but list them in one line as "also available".

## 4. Trust review (part of every install op)
For each op, state:
- **Runs code?** The exact command or entrypoint (MCP stdio command, scripts in the skill).
- **Network?** Which hosts.
- **Secrets?** Env var or Key Vault names, never values. Add placeholders to
  `.env.example`.
- **Pinned?** The commit SHA, or package version (never `latest`).
- **Provenance:** where it came from.
Flag anything unpinned, anything that runs remote code (`npx -y`, `uvx`
without a version), or anything that asks for broad filesystem access.

## 5. Apply and verify
After approval:
- **Skills:** copy into the project's skills folder and write `.source.json`.
- **MCP:** add to `.vscode/mcp.json`; check that the server lists its tools.
- **Agents:** write the `.agent.md` file.
- **A2A:** follow a2a.md.

Smoke-test each one:
- the skill's frontmatter parses
- the MCP `tools/list` returns at least one tool
- the agent card resolves

Mark failures `FAILED` in the proposal. Update `data/external-sources.md`
`Access:` to `tool:<name>` for every gap that's now closed.
