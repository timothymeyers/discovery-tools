# Remote (A2A) agents

Remote agents are **not imported**. They keep running where they are. The
project gets a registry entry, a thin local proxy agent, and a shared
transport.

## Discover
1. Fetch `<base>/.well-known/agent.json` (the agent card). Read its name,
   description, skills, input and output modes, auth schemes, and URL.
2. If there's no card, ask the user for the name, purpose and auth, and
   mark the entry `card: missing`.

## Register
Append to `.discovery/setup/a2a-agents.json` (tracked; no secrets):
```json
{"agents":[{"name":"grid-forecaster","url":"https://…","cardUrl":"https://…/.well-known/agent.json",
  "auth":{"scheme":"bearer","secretRef":"env:GRID_FORECASTER_TOKEN"},
  "skills":["load-forecast"],"registeredBy":"<proposal-id>"}]}
```
Add `GRID_FORECASTER_TOKEN=` to `.env.example`.

## Transport: the shared `a2a-bridge` MCP server
A single MCP server that reads `a2a-agents.json` and exposes these tools:
- `a2a_list_agents()`
- `a2a_send(agent, message, contextId?)`: returns a task or message
- `a2a_get_task(agent, taskId)`
- `a2a_cancel_task(agent, taskId)`

**Status: not yet built.** It's a separate deliverable, and where it should
live is still open: this project or `timothymeyers/discovery-tools`. Until
it exists:
- Propose `ADD a2a-bridge` only if a published version is found during the
  catalog review.
- Otherwise emit `REGISTER` and `PROXY` ops, label the dependent tasks
  `access-pending`, and add a task "Build or install a2a-bridge".

## Proxy agent
`.github/agents/<name>-remote.agent.md`:
```markdown
---
name: <name>-remote
description: "<card description> (remote A2A agent at <host>; delegated, not local)"
tools: [a2a-bridge/*]
---
You are a thin proxy for the remote A2A agent `<name>`.
- Never answer from your own knowledge. Forward the request with
  `a2a_send(agent="<name>", message=…)`.
- Poll with `a2a_get_task` until it reaches a terminal state, then return the
  result verbatim with its artifacts.
- On an auth error, report that `<secretRef>` is missing. Never ask for the
  secret in chat.
Remote skills: <list from card>.
```

## Trust review items
Endpoint host, auth scheme, which project data may be sent (call out
anything marked PII or controlled in `data/README.md`), and the card's
provider/organization.
