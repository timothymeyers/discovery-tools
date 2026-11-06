---
name: chat-session-experimental
description: |
  Cross-domain / Research platform observability — Find, view, and archive the Copilot chat
  sessions that belong to a project. Covers VS Code / Discovery App chat
  panels and Copilot CLI, SDK, and engine sessions. Renders any session as the
  single-page "Discovery session" chat viewer (sidebar, Thinking, Tools, and
  System noise toggles, themes) in the integrated browser. Copies session logs
  and telemetry into `.discovery/chat-sessions/` so they outlast editor
  cleanup. WHEN: find a past chat, "I had a chat with you about X, go find
  it", show or open or replay a chat session, chat transcript viewer,
  session SPA, archive chat sessions, save chat logs or telemetry to the
  project, list chat sessions for this project, chat-session-experimental.
metadata:
  version: "0.1.0-experimental"
  category: "Cross-domain"
  subfield: "Research platform observability"
---

# Chat Session Experimental

Tested with **Discovery App Preview 0.15.x on macOS**. The script also checks
VS Code, VS Code Insiders, and the Linux and Windows config folders, but those
have not been tested. Don't claim they work.

Everything except `archive` and `render` is read-only. The scripts use only the
Python standard library.

## Setup

1. Set `SKILL_DIR` to the parent folder of this `SKILL.md`. Don't assume a
   checkout location.
2. Resolve the target project's **absolute** workspace root (`ROOT`). Default
   to the current workspace folder.
3. Run `python3 "$SKILL_DIR/scripts/chat_sessions.py" <command> --workspace "$ROOT" [...]`.
   Every command prints one JSON object. Read `ok` before doing anything else.

| Command | Purpose |
|---|---|
| `list` | All sessions for the project, newest first |
| `find --query "<keywords>" [--limit 5]` | Sessions ranked by relevance, with snippets |
| `render --session <id-or-prefix> [--theme discovery\|light\|dark] [--out PATH]` | Write the chat viewer HTML and return `url` |
| `archive [--session <id>] [--dry-run]` | Copy logs and telemetry into `.discovery/chat-sessions/` |

Shared filters: `--origin all|vscode-chat|copilot-cli`, `--interactive-only`
(skips automated engine and SDK runs), and `--storage-root DIR` (extra
`workspaceStorage` folder, can be repeated). Set `COPILOT_HOME` to override
`~/.copilot`.

Read [storage layout](./references/storage-layout.md) for where sessions are
stored on disk.

## Workflows

### Find a session ("I had a chat with you about project start-up, find it")

1. Turn the request into keywords. Drop filler words such as "chat", "you",
   and "find". Example: `--query "project start up"`. The search also matches
   joined and hyphenated forms ("startup", "start-up").
2. If the user says they chatted with you, add `--interactive-only` first. If
   that finds nothing, search again without it.
3. Rank results by `coverage`, then by `score`. Show the top 1–3 with date
   (`start`), `title`, origin, and one snippet each. If there is one clear
   winner, offer to open it. If the results are close, use the ask-questions
   UI and give one option per candidate.
4. If keywords miss, run `list` and judge the sessions by their titles.

### Show a session (launch the viewer)

1. Resolve the session ID with `find` or `list`. The current conversation is
   usually the newest `vscode-chat` session.
2. Run `render --session <id>`. By default the HTML goes to
   `ROOT/.discovery/chat-sessions/<id>/chat.html`.
3. Open the returned `url` with the integrated browser tool (load it through
   tool search if it is deferred). Never open the system browser. The
   integrated browser only serves `file://` pages inside **trusted workspace
   folders**, so `--out` must stay inside `ROOT`.
4. The viewer is a static snapshot. To refresh a live session, run `render`
   again and reload the page.

### Archive all project sessions

1. Run `archive --dry-run` and summarize the results: number of sessions,
   new or updated files, and total bytes.
2. If the result includes a `warning` (git is `not-ignored`), tell the user
   that archived transcripts contain full prompts, tool arguments, outputs,
   and telemetry, and could be committed. Ask whether to add
   `.discovery/chat-sessions/` to `.gitignore`. Don't change `.gitignore`
   without consent.
3. Run `archive` and report each session's `added`, `updated`, and
   `unchanged` counts.

Archive behavior:
- It is incremental and idempotent. Files are copied only when their size or
  mtime changed.
- It never deletes anything. If the editor purges a session, the archived copy
  stays.
- It skips symlinks and `*.lock` files.
- It writes `<id>/manifest.json` and a top-level `index.json`.
- `list`, `find`, and `render` read archived sessions automatically when the
  original source is gone (`live: false, archived: true`).

## Rules

- Present session content directly. Don't narrate file paths unless the user
  asks.
- Don't delete or modify source logs under `~/.copilot` or the editor's
  `workspaceStorage`.
- Session logs can contain sensitive data. Don't copy them anywhere other than
  the project's `.discovery/chat-sessions/` unless the user asks.
