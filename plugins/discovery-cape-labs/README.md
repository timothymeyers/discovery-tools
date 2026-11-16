# discovery-cape-labs

Agent and task-design skills for Microsoft Discovery. Both overlap with skills in [microsoft/discovery](https://github.com/microsoft/discovery) and may be retired in favor of upstream later.

| Skill | What it does |
|---|---|
| [`discovery-add-agents`](skills/discovery-add-agents) | Add agents from microsoft/discovery and deploy them locally or to a Discovery project |
| [`discovery-design-tasks`](skills/discovery-design-tasks) | Write a task design using the Deterministic, Guided Exploration, or Autonomous Research pattern |

```bash
copilot plugin marketplace add timothymeyers/discovery-tools
copilot plugin install discovery-cape-labs@discovery-tools
```

For other agent hosts, install skills one at a time with
`gh skill install timothymeyers/discovery-tools <skill>`.
