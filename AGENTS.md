# AGENTS.md

Guidance for AI agents working **in** this repository. (This is not a skill — it is
repo-level context.)

## What this repo is

A public catalog of agent skills, custom agents, and starter kits for Microsoft Discovery.
The plugin root is the repo root, so `skills/` serves double duty: it is both the Agent
Skills distribution convention (`gh skill install`) and the plugin component location
(`copilot plugin install`). Do not move it, and do not create a second copy of any skill.

## Hard rules

1. **Never commit generated output.** `token_usage_mined.json` and
   `token-usage-report*.md` contain absolute local paths and full agent prompts. They are
   gitignored. If you generate one while testing, verify it is not staged.
2. **Never introduce a hardcoded install path** such as `~/.copilot/skills/...` into a
   `SKILL.md`. Skills must resolve their own payload at runtime.
3. **Never let a skill script write into its own directory.** Default to the CWD.
4. **Keep the two marketplace manifests byte-identical.** After editing
   `.github/plugin/marketplace.json`, copy it to `.claude-plugin/marketplace.json`.
5. **This repo is public.** Nothing internal, personal, or customer-identifying.
6. **Never vendor third-party source code.** External tools are referenced through
   `tools/external-tools.json` as pointers only, pinned to an immutable commit SHA
   and gated behind `requiresConfirmation: true`. If you update a pinned SHA, update
   it in both the registry and the pointer skill — CI fails if they drift.

## Before opening a PR

```bash
python3 .github/scripts/validate.py
```

## Version bumps

`plugin.json`, `.github/plugin/marketplace.json`, and `.claude-plugin/marketplace.json`
all carry a `version`. They move together with the git tag.
