# discovery-tim-labs

Skills being tried out. A skill moves to a stable plugin once it proves useful; that move is listed in the release notes, and it then disappears from this plugin.

| Skill | What it does |
|---|---|
| [`discovery-dashboard`](skills/discovery-dashboard) | Live read-only operations dashboard: progress, work front, engine liveness, CLIO investigations |
| [`discovery-project-story`](skills/discovery-project-story) | Animated, evidence-true HTML replay of how the project's work happened |
| [`chat-session-experimental`](skills/chat-session-experimental) | Find, view, and archive a project's Copilot chat sessions *(experimental)* |

```bash
copilot plugin marketplace add timothymeyers/discovery-tools
copilot plugin install discovery-tim-labs@discovery-tools
```

For other agent hosts, install skills one at a time with
`gh skill install timothymeyers/discovery-tools <skill>`.
