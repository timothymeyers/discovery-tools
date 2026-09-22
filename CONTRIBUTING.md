# Contributing

## Adding a skill

1. Create `skills/<name>/SKILL.md`. The directory name and the frontmatter `name` **must
   match**, and both must be lowercase `a-z0-9` plus hyphens (1–64 chars, no leading or
   trailing hyphen, no `--`).

2. Frontmatter follows the [Agent Skills spec](https://agentskills.io/specification):

   ```yaml
   ---
   name: my-skill
   description: |
     What it does and WHEN to trigger it. 1024 chars max. This field is what
     `gh skill search` indexes, so write it for discovery as well as for the agent.
   ---
   ```

   | Field | Required | Notes |
   |---|---|---|
   | `name` | yes | must equal the parent directory name |
   | `description` | yes | 1–1024 chars |
   | `license` | no | |
   | `compatibility` | no | max 500 chars |
   | `metadata` | no | string → string map |
   | `allowed-tools` | no | **space-separated string**, not a YAML array |

   **This list is an allow-list, not a suggestion.** The spec's reference validator
   (`skills-ref`) and Anthropic's `package_skill.py` both reject any other top-level
   key with a hard error rather than ignoring it. CI enforces the same rule.

   In particular, **do not add a top-level `version:`**. Put it under `metadata`:

   ```yaml
   metadata:
     version: "2"
   ```

   Values must be quoted strings — `metadata` is a string → string map. Bump this
   integer when a skill's behavior changes materially. The version consumers
   actually resolve is the git tag (`gh skill install skill@v1.2.0`).

3. Optional convention subdirectories: `scripts/`, `references/`, `assets/`.
   Note the **plural** `references/`.

4. Keep `SKILL.md` under ~500 lines. Agents load only `name` + `description` at startup
   and read the body on demand, so put detail in `references/` rather than inline.

## Rules for skill payloads

These are enforced by CI and by review:

- **Never hardcode an install path.** A skill may be installed as a personal skill
  (`~/.copilot/skills/`), a project skill (`.github/skills/`), or as part of a plugin
  (`~/.copilot/installed-plugins/`). Resolve script locations at runtime — see
  `skills/discovery-token-usage/SKILL.md` for the pattern.
- **Never write into the skill's own directory.** It may be read-only. Default any
  generated output to the current working directory or an explicit `--output` flag.
- **Never commit generated output.** Mined artifacts routinely contain absolute local
  paths and full agent prompts. `.gitignore` blocks the known offenders; add new ones.
- **No secrets, tokens, internal hostnames, or personal paths.** CI greps for these.

## Adding an agent

Add `agents/<name>.agent.md` with at minimum a `description` in frontmatter, then add
`"agents": "agents/"` to `plugin.json`.

## Manifests

Two files must stay byte-identical: `.github/plugin/marketplace.json` and
`.claude-plugin/marketplace.json`. After editing the first, run:

```bash
cp .github/plugin/marketplace.json .claude-plugin/marketplace.json
```

CI fails the build if they diverge.

## Validating locally

```bash
python3 .github/scripts/validate.py
```

## Releasing

Version numbers in `plugin.json` and both `marketplace.json` files move together with the
git tag.

```bash
git tag v0.2.0 && git push origin v0.2.0
gh release create v0.2.0 --generate-notes
```

`gh skill install` resolves the latest release tag by default, falling back to the default
branch, so cutting a release is what publishes a new version to consumers.
