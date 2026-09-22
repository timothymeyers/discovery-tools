# Security Policy

## Reporting a vulnerability

Report security issues through
[GitHub private vulnerability reporting](https://github.com/timothymeyers/discovery-tools/security/advisories/new).
Please do not open a public issue for a vulnerability.

## What this repo distributes

This repository distributes **agent skills**: natural-language instructions plus
executable scripts that an AI agent will run on your machine, usually with your
permissions.

Skills published here are **not verified or audited by GitHub**. Treat every skill from
every catalog — including this one — as untrusted code until you have read it.

## Before you install

1. `gh skill preview timothymeyers/discovery-tools <skill>` — read the instructions.
2. Read every file under the skill's `scripts/` directory.
3. Pin to a release tag rather than tracking the default branch:
   `gh skill install timothymeyers/discovery-tools <skill>@v0.1.0`

## Commitments for skills in this repo

- No skill pre-approves tools via `allowed-tools`. Your agent will prompt before executing.
- No skill transmits workspace data off your machine.
- No skill writes into its own installation directory.
- Generated artifacts default to the current working directory and are gitignored, because
  they can contain absolute local paths and full agent prompts.

## External tools

This catalog **points at** some third-party tools without bundling them. No third-party
source code is vendored here. Pointers live in
[`tools/external-tools.json`](tools/external-tools.json), and each one:

- is marked `bundled: false` and `requiresConfirmation: true`, so an agent must obtain
  **explicit user consent** before installing it;
- is pinned to an **immutable commit SHA or tag**, never a moving branch;
- records who verified it, when, and how.

CI enforces all of the above, including that a pointer skill and the registry cannot
silently drift to different pinned commits.

**What this does not mean.** These tools are not reviewed, audited, or warranted by this
catalog. Installing them runs third-party code on your machine, usually placing a binary
on your `PATH`. Read the upstream repository before consenting. Installing this catalog's
plugin does **not** install any external tool.

Report a vulnerability in an external tool to **its** maintainers. Report a bad or stale
pointer — a wrong pinned commit, a tool that has turned malicious, a broken install — to
this repository.

## Known data-sensitivity note

`discovery-token-usage` reads `.discovery/` workspace exhaust, which includes **full agent
prompts and absolute filesystem paths**. Its `token_usage_mined.json` output is therefore
sensitive. Do not commit it or paste it into public issues.
