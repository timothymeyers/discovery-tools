# Source types and install recommendations

Skills load only from disk: the project's `.github/skills/`, the user's
`~/.copilot/skills/` or `~/.agents/skills/`, and installed plugins. A
skill hosted elsewhere must be brought onto disk.

| Detected | How to detect | Default install | Alternatives |
|---|---|---|---|
| Skill folder | Contains `SKILL.md` with `name` and `description` | **VENDOR** into `.github/skills/<name>/` with `.source.json` | User-level copy to `~/.copilot/skills/` (tell the user it isn't in git) |
| Plugin bundle | `plugin.json` or a marketplace manifest | **INSTALL** as a plugin (shared across projects) | Vendor individual skills |
| Copilot agent | `*.agent.md` | Copy to `.github/agents/` | — |
| Discovery agent | `agent.yaml` (microsoft/discovery format) | Hand off to `discovery-add-agents` (local companion) | Foundry deploy via the same skill |
| MCP server (stdio) | `package.json` with an MCP SDK dependency, `pyproject.toml` with `mcp`, or a README "MCP server" section | **ADD** to `.vscode/mcp.json` with a pinned version | Vendor the source into `tools/<name>/` when it isn't published |
| MCP server (HTTP/SSE) | URL that responds to MCP `initialize` | **ADD** an `http` entry to `.vscode/mcp.json` | — |
| OpenAPI / REST | `openapi.json` / `swagger` | Propose a thin MCP adapter (new deliverable) | `tools` catalog registration, if supported |
| A2A agent | `/.well-known/agent.json` (agent card) | See a2a.md | — |
| GitHub repo | Any of the above inside it | Walk the tree, list everything installable, and let the user pick | — |
| Database / API with no tool | From `data/external-sources.md` | Search the catalogs for a connector MCP. If none: propose a new MCP server as a task, not an install | — |

## .source.json (written alongside every vendored item)
```json
{"repo":"timothymeyers/discovery-tools","path":"skills/foo","sha":"abc123",
 "fetchedAt":"2026-11-12T14:30:00Z","license":"MIT"}
```
On later runs, compare `sha` with upstream HEAD for that path. If it has
changed, propose `UPDATE vendored skill foo abc123 -> def456` with the
upstream diff summary.

## Local paths
Absolute paths outside the workspace are fine as a source. Vendor the item
into the project, because a path reference only works on this machine. If
the user wants a link instead, record it in `setup.json` as `local-only`.
