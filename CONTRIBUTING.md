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
     Life sciences / Enzymology — What it does and WHEN to trigger it. 1024 chars
     max. This field is what `gh skill search` indexes, so write it for discovery
     as well as for the agent.
   metadata:
     version: "1"
     category: "Life sciences"
     subfield: "Enzymology"
     secondary: "Physical sciences (chemical kinetics)"
   ---
   ```

   | Field | Required | Notes |
   |---|---|---|
   | `name` | yes | must equal the parent directory name |
   | `description` | yes | 1–1024 chars, must lead with the domain cue (see below) |
   | `license` | no | |
   | `compatibility` | no | max 500 chars |
   | `metadata` | no | string → string map; `category` and `subfield` are required here |
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

3. **Declare the scientific domain.** Every skill must say which domain it serves so
   installing users and agents can tell whether it is relevant to them. This is a
   **two-layer** convention and CI enforces both halves:

   - **Visible layer** — `description` must lead with `Category / Subfield — `.
     Agents load only `name` and `description` at startup, so a taxonomy kept solely
     in `metadata:` cannot influence routing. The cue is the part that actually works.
   - **Portable layer** — `metadata.category` and `metadata.subfield` are required,
     with optional `metadata.secondary` for a genuine second audience. These travel
     with a standalone-installed skill, which has no README to consult.

   The cue's category and `metadata.category` must match; CI fails if they drift.

   `category` comes from a controlled vocabulary (typos are the real hazard):

   | Category |
   |---|
   | `Life sciences` |
   | `Physical sciences` |
   | `Earth and environmental sciences` |
   | `Engineering and materials` |
   | `Computer and information sciences` |
   | `Mathematics and statistics` |
   | `Cross-domain` |

   `subfield` and `secondary` are free text. Use `Cross-domain` for skills that work
   regardless of scientific domain (tooling, platform, methodology, publishing) — that
   is useful information, not a fallback for "uncategorized".

   To add a category, extend `CATEGORIES` in `.github/scripts/validate.py`, this table,
   and the README grouping in the same change.

4. Optional convention subdirectories: `scripts/`, `references/`, `assets/`, `tests/`.
   Note the **plural** `references/`.

5. **Dependencies must be declared and documented.** `gh skill install` and plugin
   install do **not** install Python packages. If a skill needs third-party packages at
   runtime, add `skills/<name>/requirements.txt` and a short `## Dependencies` section in
   `SKILL.md` telling the user to install it — otherwise a standalone install fails at
   first real use. Test-only dependencies belong in the repo-root `requirements-dev.txt`.

6. **Tests, if present, must actually run.** Put `unittest` suites in `scripts/` as
   `test_*.py`, or `pytest` suites in `tests/`. CI runs both and **fails if a skill that
   ships a `tests/` directory collects zero tests** — a glob that matches nothing
   otherwise passes silently. Keep suites offline: inject HTTP via a fake callable
   reading from `fixtures/`, never hit the network from a test.

7. Keep `SKILL.md` under ~500 lines. Agents load only `name` + `description` at startup
   and read the body on demand, so put detail in `references/` rather than inline.

8. **Add your skill to the README skills table**, in the section for its category. CI
   fails if a skill is missing from the README or if a category has no section.

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

## Adding an external tool

To make a third-party tool discoverable without bundling it, add an entry to
[`tools/external-tools.json`](tools/external-tools.json). **Never vendor third-party
source code into this repository.**

That file is the source of truth. Required fields: `name`, `description`,
`repository` (an `https://github.com/` URL), `license`, `author`, and `install`.
CI additionally enforces:

- `bundled: false` — this catalog vendors no third-party code.
- `requiresConfirmation: true` — agents must get explicit user consent before
  installing. These tools execute on the user's machine.
- `install.sourceRef` pinned to a **full 40-character commit SHA** (or a tag).
  Never a moving branch.
- `install.steps` and `install.verify` present — and **actually tested**. Install
  the tool from the pinned ref into a throwaway prefix and confirm the verify
  command succeeds before you commit the entry. Record what you did in `verified`.
- If you set `relatedSkill`, that skill must exist, must link the upstream
  repository, and must contain the same pinned SHA. This stops the registry and
  the skill from drifting apart.

A pointer skill is optional but recommended — it is what teaches an agent *when*
to reach for the tool. Write your own prose. Do not copy upstream documentation
wholesale; link to the upstream README **at the pinned commit** for the
authoritative reference, and summarize only what the agent needs.

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
