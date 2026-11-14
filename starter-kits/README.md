# Starter kits

Project templates for getting a Microsoft Discovery project off the ground.

| Kit | What it sets up |
|---|---|
| [`discovery-project-kit`](discovery-project-kit) | Enables the `discovery-project-kit` plugin (six project-operations skills) and optionally installs the pinned upstream `token-estimator-mvp` skill from microsoft/discovery |

Starter kits are **not** plugin components — they are project templates, so they are
not installed by `copilot plugin install` or `gh skill install`. Copy a kit's
`template/` directory into your project; its README says how.

Each kit should ship a `template/.github/copilot/settings.json` that registers this
catalog and enables the plugin it needs, so everyone who opens the project gets the
skills:

```json
{
  "extraKnownMarketplaces": {
    "discovery-tools": {
      "source": { "source": "github", "repo": "timothymeyers/discovery-tools" }
    }
  },
  "enabledPlugins": { "discovery-project-kit@discovery-tools": true }
}
```

A kit that installs skills from an external catalog must pin them to that catalog's
`sourceRef` in [`tools/external-catalogs.json`](../tools/external-catalogs.json) and
ask before installing. CI fails if the two drift.
