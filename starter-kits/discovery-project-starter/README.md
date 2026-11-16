# discovery-project-starter

A starting point for a Microsoft Discovery project. It turns on the
[`discovery-project-bundle`](../../plugins/discovery-project-bundle) plugin for
everyone who opens the project, and points to two upstream skill catalogs worth
browsing.

## What's in it

**Turned on for the project:**

| Plugin | Skills |
|---|---|
| [`discovery-project-bundle`](../../plugins/discovery-project-bundle) | `discovery-status-board`, `engine-chat-experimental`, `discovery-token-usage`, `discovery-gather-exhaust` |

**Upstream catalogs to browse.** The kit installs nothing from them:

| Catalog | What's there |
|---|---|
| [microsoft/discovery](https://github.com/microsoft/discovery/tree/d424c9e7e6b769fdfdbe0c63bea4ad8ff7a82ecf) | Microsoft's Discovery catalog: science agents, domain starter kits, and skills including `build-research-paper` and `token-estimator-mvp` |
| [AI-ModCon/genesis-skills](https://github.com/AI-ModCon/genesis-skills/tree/b7e8434f3cd15a0c8cb6cb0313942cd4b1d06cee) | DOE Genesis Mission skills: HPC job submission, plasma simulation, scientific data, AI evaluation and safety, data movement, literature search |

Both links are pinned to the commits recorded in
[`tools/external-catalogs.json`](../../tools/external-catalogs.json).

## Use it

### 1. Add the template to your project

From your project root:

```bash
gh repo clone timothymeyers/discovery-tools /tmp/discovery-tools -- --depth 1
cp -Rn /tmp/discovery-tools/starter-kits/discovery-project-starter/template/. .
```

`-n` keeps any file you already have. This adds
`.github/copilot/settings.json`, which registers this marketplace and turns on
`discovery-project-bundle`. If you already have that file, merge the
`extraKnownMarketplaces` and `enabledPlugins` keys in by hand.

To install the plugin for yourself right away, without the template:

```bash
copilot plugin marketplace add timothymeyers/discovery-tools
copilot plugin install discovery-project-bundle@discovery-tools
```

### 2. Optional: add skills from the upstream catalogs

Neither catalog publishes a plugin marketplace yet, so add the skills you want
one at a time, pinned to the reviewed commit. Preview first:

```bash
gh skill preview microsoft/discovery skills/<skill>@d424c9e7e6b769fdfdbe0c63bea4ad8ff7a82ecf
gh skill install microsoft/discovery skills/<skill> --pin d424c9e7e6b769fdfdbe0c63bea4ad8ff7a82ecf

gh skill preview AI-ModCon/genesis-skills skills/<scope>/<skill>@b7e8434f3cd15a0c8cb6cb0313942cd4b1d06cee
gh skill install AI-ModCon/genesis-skills skills/<scope>/<skill> --pin b7e8434f3cd15a0c8cb6cb0313942cd4b1d06cee
```

Always pass `--pin`: neither repository publishes release tags, so an unpinned
install tracks a moving `main`. Check each genesis skill's own license; see the
[README notes](../../README.md#external-catalogs).

> [!CAUTION]
> Upstream skills are third-party prompts your agent will follow and scripts it
> may run. They are not reviewed or warranted by this catalog. Read the safety
> notes in [`tools/external-catalogs.json`](../../tools/external-catalogs.json)
> before installing.

When either catalog publishes a `marketplace.json`, this kit will register it
in `settings.json` instead.
