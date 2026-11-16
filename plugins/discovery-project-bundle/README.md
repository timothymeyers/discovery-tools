# discovery-project-bundle

The stable set of skills for running one Microsoft Discovery project.

| Skill | What it does |
|---|---|
| [`discovery-clarify-project`](skills/discovery-clarify-project) | Before work starts: asks up to 5 clarifying questions about the project's purpose, outcomes, and tasks |
| [`discovery-status-board`](skills/discovery-status-board) | Outcome grades as red/yellow/green chips, plus a task kanban |
| [`engine-chat-experimental`](skills/engine-chat-experimental) | Read engine runs as a chat *(experimental)* |
| [`discovery-token-usage`](skills/discovery-token-usage) | Token usage across engines, models, and chat |
| [`discovery-gather-exhaust`](skills/discovery-gather-exhaust) | Copy session transcripts, logs, and telemetry into the project folder |

```bash
copilot plugin marketplace add timothymeyers/discovery-tools
copilot plugin install discovery-project-bundle@discovery-tools
```

For other agent hosts, install skills one at a time with
`gh skill install timothymeyers/discovery-tools <skill>`.

To set this up for a whole project team, use the [`discovery-project-starter`](../../starter-kits/discovery-project-starter) starter kit.
