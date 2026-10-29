# Engine Chat operations

Engine Chat version: **0.1.0-experimental**. This package was tested with
**Discovery 0.15.19 on macOS**. Other Discovery versions and operating systems
are untested; this package makes no support claim for them.

## Safety and privacy model

- The manager requires an explicit workspace containing `.discovery`.
- Installation is project-local at
  `.discovery/engine-chat-experimental/runtime` and is ignored with the rest of
  `.discovery`.
- The service binds only to `127.0.0.1` and never opens a system browser.
- Engine Chat is read-only. Steering is disabled.
- User-authored starting prompts and delegated-agent prompts from local logs are
  visible. Raw action inputs, outputs, and error payloads are intentionally
  omitted.
- Runtime state and logs stay under `.discovery/engine-chat-experimental/`.
- The shipped runtime uses only the Python standard library.

## Portable command construction

Resolve the directory containing the loaded skill file and construct the
absolute manager path as `SKILL_DIR/scripts/engine_chat.py`. Always pass the
absolute target workspace:

- Inspect: `python3 <manager> status --workspace <root> --json`
- Install/update: `python3 <manager> install --workspace <root> --json`
- Explicit repair: `python3 <manager> install --workspace <root> --repair --json`
- Launch: `python3 <manager> launch --workspace <root> --json`
- Restart a verified matching instance: `python3 <manager> launch --workspace
  <root> --restart --json`

Run launch in an asynchronous visible terminal. It remains in the foreground
until stopped. A successful launch emits one JSON object with `ok: true`, a
stable loopback `url`, the selected port, PID, and whether an existing matching
service was reused. Open that URL with the integrated browser.

## Status interpretation

`status` returns both `installation` and `runtime` objects.

Installation states:

- `missing`: no installed runtime or metadata exists.
- `current`: installed payload hashes and metadata match this package.
- `outdated`: the installed payload is internally intact but an older package
  version or manifest is recorded; a normal install safely updates it.
- `modified`: installed files differ from their recorded hashes or contain
  unexpected payload files; explicit `--repair` is required.
- `incomplete`: runtime, metadata, or recorded payload files are missing or
  malformed; explicit `--repair` is required.

Runtime states:

- `running`: health identity matches the service ID, protocol, Engine Chat
  version, workspace fingerprint, and PID.
- `stopped`: no matching service was found. Safely identifiable dead or
  malformed run records may have been removed; inspect `staleStateCleaned`.
- `unverified`: a live PID or foreign record could not be matched. Do not kill
  it. Report the warning and use another available port unless the user asks
  for manual investigation.

Status scans ports `8765` through `8785` when needed, even if `run.json` is
absent. A discovered matching instance is reused and its state is reconstructed.

## Install and repair behavior

A normal current install is a no-op. Missing installs are copied from the skill
assets through a verified staging directory. Intact outdated installs are
atomically replaced.

Never overwrite `modified` or `incomplete` installations without the user's
repair consent. `--repair` records the prior metadata and moves any prior
runtime into a timestamped directory beneath
`.discovery/engine-chat-experimental/backups/` before replacement.

After install or repair, inspect the returned `stateAfter`; it must be
`current` before launch.

## Launch and restart behavior

Launch requires a `current` installation. It first reuses a matching service if
one exists. Otherwise it prefers `127.0.0.1:8765` and walks forward through the
bounded range when a port is occupied; the result lists collisions and the
selected port.

Do not use a system-browser flag or background the process inside the manager.
The calling agent starts the foreground manager in an asynchronous visible
terminal, waits for its JSON readiness result, and opens the returned URL in
the integrated browser.

`--restart` is not a general stop operation. It sends `SIGTERM` only after a
fresh health check confirms the same service ID, protocol/version, workspace
fingerprint, and PID. Owned run state is removed on graceful `Ctrl+C` or
`SIGTERM`; a replacement process cannot have its state removed by the old one
because cleanup rechecks PID and workspace identity.

## Failure handling

- Missing `.discovery`: stop and report that the target is not a valid prepared
  Discovery project; do not create it.
- Bundle hash failure: stop. The tracked skill payload itself is inconsistent
  and must be fixed before installation.
- No free bounded port: report every occupied port and do not launch.
- Service exits before readiness: report the exit code and leave no owned stale
  run record.
- `modified` or `incomplete` without consent: report `requiresRepair: true` and
  ask before rerunning with `--repair`.
