---
name: engine-chat-experimental
description: |
  Cross-domain / Research platform observability — Install, verify,
  repair, update, and launch the experimental Engine Chat runtime for
  Discovery engine runs, then open an existing or newly launched
  instance in the integrated browser. Use when: Engine Chat, inspect
  engine activity, open engine conversations, install Engine Chat,
  launch Engine Chat, open a running Engine Chat instance, or repair a
  project-local Engine Chat runtime.
metadata:
  version: "0.1.0-experimental"
  category: "Cross-domain"
  subfield: "Research platform observability"
---

# Engine Chat Experimental

Use this skill to install or launch **Engine Chat 0.1.0-experimental** in a
Discovery project. It was tested with **Discovery 0.15.21 on macOS**. Other
Discovery versions and operating systems are untested; do not make support or
compatibility claims for them.

Engine Chat is local and read-only. It displays user-authored starting prompts
and delegated-agent prompts found in local Discovery logs. Steering remains
disabled, and raw action, output, and error payloads remain hidden.

## Required setup

1. Resolve this loaded `SKILL.md` file's parent directory as `SKILL_DIR`. Never
   assume a checkout location or the current working directory.
2. Resolve the target project's explicit absolute workspace root. It must
   already contain a `.discovery` directory.
3. Invoke `python3 "$SKILL_DIR/scripts/engine_chat.py" <operation> --workspace
   <absolute-root> --json`.
4. Open Engine Chat only in the integrated browser, using the URL returned by
   the manager. Never request a system-browser launch.

Read [operational details](./references/operations.md) when executing the
workflow.

## Invocation routing

### Bare `/engine-chat-experimental`

Run `status` **before asking any question**. Status verifies installation
integrity, cleans safely identifiable stale run state, and searches the bounded
local port range for a matching instance. Then use the ask-questions UI with
these state-specific choices (always at least two options):

- Matching instance running: **Open existing**, **Restart**, **Cancel**.
- Current installation stopped: **Launch**, **Verify or reinstall**, **Cancel**.
- Missing installation: **Install and launch**, **Install only**, **Cancel**.
- Outdated, modified, or incomplete installation: **Repair/update and launch**,
  **Repair/update only**, **Cancel**.
- Unverified live process record: report the warning and offer **Launch on an
   available port** or **Cancel**; never stop the unverified PID.

Do not ask a generic question before status is known. A safely cleaned stale
record is treated as stopped.

### Explicit `install`

Inspect status. Install directly when missing, safely outdated, or already
current. A current install is an idempotent no-op. If status is `modified` or
`incomplete`, obtain explicit repair consent before invoking `install --repair`;
repair preserves a backup.

### Explicit `launch`

Inspect status first. Reuse a matching running instance, or launch a current
stopped installation in an asynchronous visible terminal. If installation is
missing, **offer installation**; never install silently. If it is modified,
incomplete, or outdated, obtain repair/update consent before changing it.

Keep the launch terminal visible because the service runs in the foreground.
Wait for the manager's machine-readable successful result, then open its `url`
in the integrated browser. Use `launch --restart` only after the user chooses
**Restart**.
