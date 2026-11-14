# discovery-project-kit plugin

Six skills from this catalog for running one Microsoft Discovery project:

- `discovery-add-agents` — add and deploy agents from microsoft/discovery
- `discovery-status-board` — outcome grades and a task kanban
- `engine-chat-experimental` — read engine runs as a chat
- `discovery-gather-exhaust` — save transcripts, logs, and telemetry into the project
- `discovery-token-usage` — token usage across engines and models
- `build-research-paper` — arxiv-ready PDF from the results

```bash
copilot plugin marketplace add timothymeyers/discovery-tools
copilot plugin install discovery-project-kit@discovery-tools
```

`skills/` holds symlinks into the repository's root `skills/`; installers copy the
real files. For the matching project template and the pinned upstream token
estimator, see [`starter-kits/discovery-project-kit`](../../starter-kits/discovery-project-kit).
