# discovery-tools

**Agents, skills, and starter kits for [Microsoft Discovery](https://azure.microsoft.com/en-us/products/microsoft-discovery) — the AI for Science platform.**

This repo is a catalog. It is simultaneously:

- an **[Agent Skills](https://agentskills.io/specification)** source — every skill under
  `skills/` is individually installable into ~50 agent hosts with `gh skill install`
- a **Copilot CLI plugin marketplace** — `copilot plugin marketplace add timothymeyers/discovery-tools`
- a **Claude Code plugin marketplace** — `/plugin marketplace add timothymeyers/discovery-tools`

Everything works from a single physical copy of each skill: the plugin root is the repo
root, so `skills/` satisfies both the Agent Skills distribution convention and the plugin
component layout. No symlinks, no duplication, no build step.

---

## Catalog

### Skills

| Skill | Version | What it does |
|---|---|---|
| [`discovery-token-usage`](skills/discovery-token-usage) | 1 | Mines a Discovery workspace's `.discovery/` exhaust for token usage across engines (Clio / Science Engine, copilot-cli / Mission Control), models, and interactive chat. Handles seven known data-quality traps. Cost reporting is a deliberate opt-in feature flag. |
| [`build-research-paper`](skills/build-research-paper) | 2 | Builds publication-quality, arxiv-ready PDFs from LaTeX or Markdown + figures, using `tectonic` (default) or `pandoc` (escape hatch). Ships templates, a scaffolder, build and check scripts, and referee-revision scaffolding. |
| [`osti-literature-search`](skills/osti-literature-search) | 1 | Searches and retrieves DOE-funded literature from OSTI.GOV. **Pointer skill** — drives the external `osti-axi` CLI, which is not bundled here. |

### External tools

Tools this catalog makes discoverable but does **not** bundle. No third-party
source code is vendored in this repository — each entry is a pointer plus the
metadata an agent needs to evaluate, install, and drive the tool.

| Tool | Author | License | Pointer skill |
|---|---|---|---|
| [`osti-axi`](https://github.com/davenovelli-pnnl/osti-axi) — OSTI.GOV search and full-text retrieval CLI for agents | Dave Novelli (PNNL) | MIT | [`osti-literature-search`](skills/osti-literature-search) |

The machine-readable registry is [`tools/external-tools.json`](tools/external-tools.json).
It is the source of truth: it carries the pinned commit, runtime prerequisites,
verified install steps, and safety metadata, and CI enforces that any pointer
skill agrees with it.

> [!WARNING]
> External tools are **third-party code executed on the user's machine**, and
> are not reviewed or warranted by this catalog. Every entry is marked
> `requiresConfirmation: true` — agents must obtain explicit user consent
> before installing one. Installing this catalog's plugin does **not** install
> any external tool.

### Agents

None yet. Custom agents will live in `agents/` as `*.agent.md`.

### Starter kits

None yet. See [`starter-kits/`](starter-kits).

---

## Install

### Any agent — individual skills

Works with GitHub Copilot, Claude Code, Cursor, Codex, Gemini CLI, opencode, Warp, and
others. Requires **GitHub CLI 2.90.0+**.

```bash
# Browse what's here
gh skill search discovery --owner timothymeyers

# ALWAYS inspect before installing
gh skill preview timothymeyers/discovery-tools discovery-token-usage

# Install one skill
gh skill install timothymeyers/discovery-tools discovery-token-usage

# ...or everything
gh skill install timothymeyers/discovery-tools --all

# Target a different agent host
gh skill install timothymeyers/discovery-tools discovery-token-usage \
  --agent claude-code --scope user

# Keep up to date
gh skill update --all
```

### GitHub Copilot CLI — full plugin

Installs every skill (and, later, agents and MCP servers) as one bundle.

```bash
copilot plugin marketplace add timothymeyers/discovery-tools
copilot plugin browse discovery-tools          # optional
copilot plugin install discovery-tools@discovery-tools
```

Or skip the marketplace entirely:

```bash
copilot plugin install timothymeyers/discovery-tools
```

In VS Code: Extensions view → search `@agentPlugins`, or Command Palette → **Chat: Plugins**.

### Claude Code — full plugin

```
/plugin marketplace add timothymeyers/discovery-tools
/plugin install discovery-tools@discovery-tools
```

### Team-wide (no per-developer commands)

Commit this to your own repo at `.github/copilot/settings.json`. Copilot CLI, the Copilot
cloud agent, and Copilot code review all pick it up automatically:

```json
{
  "extraKnownMarketplaces": {
    "discovery-tools": { "source": "timothymeyers/discovery-tools" }
  },
  "enabledPlugins": ["discovery-tools@discovery-tools"]
}
```

### Manual

Copy any `skills/<name>/` directory into one of the locations your agent scans:

| Scope | Locations |
|---|---|
| Project | `.github/skills/`, `.claude/skills/`, `.agents/skills/` |
| Personal | `~/.copilot/skills/`, `~/.agents/skills/` |

Then run `/skills reload` in Copilot CLI.

---

## Security

> [!WARNING]
> Agent skills are **not verified by GitHub**. A skill is a prompt plus executable code
> that your agent will run on your machine. Skills from any source — including this one —
> can contain prompt injections, hidden instructions, or malicious scripts.

Before installing anything from this or any other catalog:

1. Run `gh skill preview OWNER/REPO SKILL` and actually read the output.
2. Read any files under `scripts/`.
3. Prefer pinning to a release tag: `gh skill install ... discovery-token-usage@v0.1.0`.

Skills in this repo do not pre-approve any tools via `allowed-tools`; your agent will
prompt you before running anything. Report issues per [SECURITY.md](SECURITY.md).

---

## Repo layout

```
discovery-tools/
├── plugin.json                     # plugin manifest — plugin root IS the repo root
├── skills/<name>/SKILL.md          # the catalog; installable via `gh skill install`
├── tools/external-tools.json       # registry of external tools (pointers, never vendored)
├── agents/<name>.agent.md          # custom agents (none yet)
├── starter-kits/<name>/            # project scaffolds (none yet)
├── .github/plugin/marketplace.json # Copilot CLI marketplace manifest
├── .claude-plugin/marketplace.json # byte-identical copy for Claude Code; CI enforces sync
└── .github/copilot/settings.json   # this repo dogfoods its own plugin
```

Why `.claude-plugin/marketplace.json` is a real file rather than a symlink (which is what
`github/copilot-plugins` uses): git symlinks degrade to plain text files on Windows clones
and in downloaded ZIPs. CI asserts the two files stay byte-identical.

## Versioning

Two different things carry versions here, and they are not the same:

- **The catalog** (`plugin.json` + both `marketplace.json` files) is versioned with
  semver, in lockstep with the git tag. This is what `copilot plugin install` sees.
- **Each skill** carries `metadata.version` in its `SKILL.md` frontmatter — a simple
  integer that increments when the skill's behavior changes materially.

> [!IMPORTANT]
> Skill versions go under `metadata:`, **never** as a top-level `version:` key.
> The Agent Skills spec defines an allow-list of exactly six top-level fields
> (`name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`).
> The spec's reference validator and Anthropic's packaging script both treat any
> other top-level key as a **hard error rather than ignoring it**, so a stray
> `version:` breaks `.skill` packaging and claude.ai upload. `metadata` is the
> spec's designated extension point: a string → string map that clients ignore.

```yaml
---
name: my-skill
description: |
  ...
metadata:
  version: "1"
---
```

Note the quotes — `metadata` values must be **strings**, not numbers.

For consumers, the version of record is still the **git tag**: `gh skill install`
resolves `skill@v1.2.0` from releases, not from frontmatter.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). New skills go in `skills/<name>/SKILL.md` with the
`name` field matching the directory name.

## Why not GitHub Marketplace?

[GitHub Marketplace](https://docs.github.com/en/apps/publishing-apps-to-github-marketplace/github-marketplace-overview/about-github-marketplace)
lists **Actions and Apps only** — there is no listing category for Agent Skills, custom
agents, or plugins. Distribution for those happens through the plugin-marketplace system,
`gh skill search` (which searches `SKILL.md` content via code search), and repo topics.

## License

[MIT](LICENSE)
