# discovery-project-kit

A focused toolset for running a Microsoft Discovery project: deploy catalog
agents, watch outcomes and engine runs, keep the project's records, and write it
up.

The kit has two parts:

- **The [`discovery-project-kit`](../../plugins/discovery-project-kit) plugin**, which installs six
  skills from this catalog.
- **This template**, which turns that plugin on for a project and can add one
  pinned skill from [microsoft/discovery](https://github.com/microsoft/discovery).

## What's in it

| Skill | Source | Use it to |
|---|---|---|
| [`discovery-add-agents`](../../skills/discovery-add-agents) | this catalog | Add agents from microsoft/discovery and deploy them locally or to a Discovery project |
| [`discovery-status-board`](../../skills/discovery-status-board) | this catalog | See outcome grades and a task kanban |
| [`engine-chat-experimental`](../../skills/engine-chat-experimental) | this catalog | Read engine runs as a chat |
| [`discovery-gather-exhaust`](../../skills/discovery-gather-exhaust) | this catalog | Save session transcripts, logs, and telemetry into the project |
| [`discovery-token-usage`](../../skills/discovery-token-usage) | this catalog | Report token usage across engines and models |
| [`build-research-paper`](../../skills/build-research-paper) | this catalog | Build an arxiv-ready PDF from the results |
| [`token-estimator-mvp`](https://github.com/microsoft/discovery/tree/d424c9e7e6b769fdfdbe0c63bea4ad8ff7a82ecf/skills/token-estimator-mvp) | microsoft/discovery, pinned | Estimate token use before the work starts (Node.js) |

## Use it

### 1. Install the plugin

```bash
copilot plugin marketplace add timothymeyers/discovery-tools
copilot plugin install discovery-project-kit@discovery-tools
```

For other agent hosts, install the six skills one at a time with
`gh skill install timothymeyers/discovery-tools <skill>`.

### 2. Add the template to your project

From your project root:

```bash
gh repo clone timothymeyers/discovery-tools /tmp/discovery-tools -- --depth 1
cp -Rn /tmp/discovery-tools/starter-kits/discovery-project-kit/template/. .
```

`-n` keeps any file you already have. This adds:

- `.github/copilot/settings.json`, which registers the marketplace and enables
  the plugin for everyone who opens the project. If you already have this file,
  merge the two keys in by hand.
- `scripts/install-upstream-skills.sh`, the installer for the upstream skill.

### 3. Optional: add the upstream token estimator

Review it first, then install:

```bash
gh skill preview microsoft/discovery skills/token-estimator-mvp@d424c9e7e6b769fdfdbe0c63bea4ad8ff7a82ecf
scripts/install-upstream-skills.sh
```

It asks before installing, refuses to run unattended without `--yes`, and
installs at the pinned commit into `.agents/skills/`. `--agent <host>` targets a
different agent host; `--dry-run` prints the command only.

> [!CAUTION]
> `token-estimator-mvp` is third-party: a prompt your agent will follow and
> scripts it may run. It is not reviewed or warranted by this catalog. See the
> [`microsoft-discovery` registry entry](../../tools/external-catalogs.json) for
> the pinned commit and safety notes.

## Why some skills aren't here

- **Upstream `add-example-agents`, `build-research-paper`, `discovery-design-tasks`:**
  this catalog has its own copies. The kit uses ours.
- **Upstream `discovery-catalog`:** it lists only the repository it runs in, so
  in a normal Discovery project it finds nothing.
