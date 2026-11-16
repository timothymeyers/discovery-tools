---
name: discovery-gather-exhaust
description: >-
  Cross-domain / Research platform observability — Gather a Discovery project's
  "exhaust" that lives outside the project folder (Copilot CLI/SDK agent and
  engine session transcripts, editor chat sessions/transcripts/debug logs,
  Copilot process logs, Discovery App service logs, token-usage telemetry, app
  terminal logs) into `.discovery/exhaust/` in documented folders, scrub it
  to the privacy level the user picks, handle large files (archive / Git LFS /
  Azure Blob / skip), and commit it to git. Safe to run any time; only adds
  or updates, never deletes. WHEN: "gather exhaust", "save the agent logs to
  the project", "archive telemetry", "copy session logs into .discovery",
  "preserve chat history in git", "collect agent run transcripts",
  "discovery-gather-exhaust". DO NOT USE FOR: token read-outs
  (discovery-token-usage) or viewing chats (chat-session-experimental).
metadata:
  version: "1"
  category: "Cross-domain"
  subfield: "Research platform observability"
---

# discovery-gather-exhaust

Copies everything Discovery and Copilot record about this project outside the
project into `.discovery/exhaust/`, then commits it. Run only when the user asks.

Resolve this loaded `SKILL.md` file's parent directory as `SKILL_DIR`. Never
assume a checkout location. Run the script from the project root (the folder
that contains `.discovery/`), or pass `--workspace <absolute-root>`.

Script: `$SKILL_DIR/scripts/gather.py`
(Python 3.12+, standard library only — call `python3.12` explicitly on macOS).

## What it gathers

| Folder in `.discovery/exhaust/` | Source | Match rule |
|---|---|---|
| `agent-sessions/<uuid>/` | `~/.copilot/session-state/<uuid>/` | `cwd` or `git_root` in `workspace.yaml` is this project or inside it |
| `chat/{sessions,editing-sessions,transcripts,debug-logs,tool-outputs,memory}/` | `<app>/user-data/User/workspaceStorage/<hash>/` | `<hash>` whose `workspace.json` points at this project |
| `logs/copilot-process/` | `~/.copilot/logs/*.log` | mentions a project run ID or the project path |
| `logs/app-terminal/<date>.log` | `<app>/user-data/logs/<date>/terminal.log` | only matching lines |
| `telemetry/discovery-express.jsonl` | `<app>/telemetry/logs/discovery-express.jsonl*` | only lines with a run ID or project path, de-duplicated across rotations |
| `telemetry/token-usage.jsonl` | `<app>/telemetry/token-usage.jsonl` | lines inside run/session time windows, tagged `_gather_match: time-window` |

Plus `manifest.json` (every file: source, sha256, privacy, match), `links.json`
(agent run → session folders, chats, logs; `run-id` or `time+task` match),
`config.json` (saved choices) and a generated `README.md`.

## Workflow

1. **Dry run** (default; writes nothing):
   `python3.12 "$SKILL_DIR/scripts/gather.py"`
   Report the per-category counts, total MB, redaction counts and the
   **sensitive** list (Work IQ sessions and chats containing M365 content).
2. **Ask the user for the privacy setting** with the ask-questions tool,
   unless `config.json` already has one and they did not ask to change it:
   - `raw` — copy as-is (secrets are still always redacted)
   - `scrub` (default) — also replace emails, phone numbers and listed names
     with stable placeholders (`<person-1a2b3c4d>`). Ask whether to add names
     (`--name "First Last"`, repeatable, saved to config).
   - `exclude-sensitive` — leave sensitive items out entirely
3. **Large files.** If the dry run exits `3` / prints `LARGE`, present the
   choices for each file and save them:
   `archive` (gzip, plain git), `lfs` (Git LFS for that path), `blob`
   (Azure Blob upload + pointer file; needs `--blob-account`/`--blob-container`
   and `az login`), `skip`. Use `--large "<glob>=<choice>"` or
   `--large-file-policy <choice>`. Plain git is refused over 100 MB.
4. **Apply and commit:**
   `python3.12 "$SKILL_DIR/scripts/gather.py" --apply --privacy <setting>`
   It stages only `.discovery/exhaust/` (and `.gitattributes` if LFS was
   chosen) and commits with a summary message. It never pushes, never uses
   `--no-verify`, and makes no commit when nothing changed. If a hook or
   signing fails, report the error; files stay staged. `--no-commit` stages only.
5. Report the commit hash and summary to the user.

## Rules

- Never delete anything under `.discovery/exhaust/`. Rotated or purged sources
  stay preserved; log files are merged line by line.
- A file gathered under a stricter privacy setting is not replaced by a less
  strict run unless the user explicitly asks (`--allow-downgrade`).
- The scrub salt lives at `~/.config/discovery-gather-exhaust/salt` (never in
  the repo) so placeholders are stable across runs.
- Other options: `--since YYYY-MM-DD`, `--include`/`--exclude`
  (`agent-sessions,chat,copilot-process,app-terminal,discovery-express,token-usage`),
  `--json` for a machine-readable plan.
- The current chat is captured as of the moment it runs; re-run later to
  update it.

## Tests

`cd "$SKILL_DIR" && python3.12 -m unittest discover -s tests`
