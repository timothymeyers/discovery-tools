# Starter kits

Project templates for getting a Microsoft Discovery project off the ground.

| Kit | What it sets up |
|---|---|
| [`discovery-project-starter`](discovery-project-starter) | Turns on the `discovery-project-bundle` plugin for the project, and points to the microsoft/discovery and genesis-skills catalogs at their pinned commits |

Plugins can't depend on other plugins, so a starter kit is how plugins are
combined: its `template/.github/copilot/settings.json` registers this
marketplace and turns on the plugins the project needs. Everyone who opens the
project gets them.

```json
{
  "extraKnownMarketplaces": {
    "discovery-tools": {
      "source": { "source": "github", "repo": "timothymeyers/discovery-tools" }
    }
  },
  "enabledPlugins": { "discovery-project-bundle@discovery-tools": true }
}
```

Starter kits are **not** plugin components, so `copilot plugin install` and
`gh skill install` don't install them. Copy a kit's `template/` directory into
your project; its README says how.

CI checks that every kit has a README and a `settings.json`, that each plugin it
turns on exists in this marketplace, and that any commit it pins for an external
catalog matches [`tools/external-catalogs.json`](../tools/external-catalogs.json).
