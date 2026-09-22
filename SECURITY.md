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

## Known data-sensitivity note

`discovery-token-usage` reads `.discovery/` workspace exhaust, which includes **full agent
prompts and absolute filesystem paths**. Its `token_usage_mined.json` output is therefore
sensitive. Do not commit it or paste it into public issues.
