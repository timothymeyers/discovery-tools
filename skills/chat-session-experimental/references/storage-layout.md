# Where chat sessions live

Chat history is **not** all in `~/.copilot`. There are two independent stores.
`chat_sessions.py` searches both, plus the project archive.

## 1. Editor chat panels (VS Code, Insiders, Discovery App)

Each editor keeps per-workspace storage:

| OS | User data root |
|---|---|
| macOS | `~/Library/Application Support/<App>/User/` or `.../<App>/user-data/User/` (Discovery App) |
| Linux | `~/.config/<App>/User/` |
| Windows | `%APPDATA%\<App>\User\` |

Each folder under `workspaceStorage/<hash>/` contains a `workspace.json` with
`{"folder": "file:///abs/project"}`. A storage folder belongs to the project
when that URI resolves exactly to the project root. Multi-root
`.code-workspace` folders are checked as well. Per-session artifacts inside
the storage folder:

| Artifact | Path | Archived as |
|---|---|---|
| Event transcript (rendered) | `GitHub.copilot-chat/transcripts/<id>.jsonl` | `vscode/transcript.jsonl` |
| UI model (question answers) | `chatSessions/<id>.jsonl` (older: `.json`) | `vscode/chat-session.jsonl` |
| Debug telemetry (spans, models) | `GitHub.copilot-chat/debug-logs/<id>/` | `vscode/debug-logs/` |
| Large tool results | `GitHub.copilot-chat/chat-session-resources/<id>/` | `vscode/chat-session-resources/` |
| Edit snapshots | `chatEditingSessions/<id>/` | `vscode/chat-editing-session/` |

The editor removes old sessions from this store. Archive sessions you want to
keep.

## 2. Copilot CLI / SDK / engine sessions

`$COPILOT_HOME/session-state/<id>/` (default `~/.copilot`). `workspace.yaml`
records `cwd` and `client_name`. A session belongs to the project when `cwd`
is the root or a folder below it.

`client_name` values:
- `cli`: interactive.
- `sdk` and `discovery-express`: automated engines and sub-agents.

The folder contains `events.jsonl` (same event schema as editor transcripts),
`session.db`, `checkpoints/`, `files/`, `research/`, and `plan.md`. The whole
folder is archived as `copilot-cli/session-state/`.

## 3. Project archive

```
.discovery/chat-sessions/
  index.json                 # summary of every archived session
  <id>/manifest.json         # origin, title, times, counts, source paths
  <id>/vscode/...            # editor artifacts (see above)
  <id>/copilot-cli/...       # CLI session-state copy
  <id>/chat.html             # viewer written by `render` (default location)
```

## Transcript event schema (shared by both stores)

The viewer uses these event types:
- `session.start`
- `user.message` (`data.content`)
- `assistant.message` (`data.content`, `data.reasoningText`)
- `tool.execution_start` (`toolName`, `arguments`, `toolCallId`)
- `tool.execution_complete` (`success`)
- `assistant.turn_start`

For `vscode_askQuestions` answers, the viewer looks up `questionCarousel`
nodes in the chatSessions file, keyed `<toolCallId>:<questionIndex>`.
User messages that start with `[Terminal ` or `Continue: ` are treated as
system noise.
