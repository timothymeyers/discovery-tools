# Starter kits

Project scaffolds for getting a new Microsoft Discovery workload off the ground.

Nothing here yet.

Starter kits are **not** plugin components — they are whole project templates, so they are
not installed by `copilot plugin install` or `gh skill install`. They are meant to be
copied, or exposed through a scaffolding skill that generates them.

Each kit should ship a `.github/copilot/settings.json` that registers this catalog, so a
project created from a kit gets the Discovery skills automatically:

```json
{
  "extraKnownMarketplaces": {
    "discovery-tools": { "source": "timothymeyers/discovery-tools" }
  },
  "enabledPlugins": ["discovery-tools@discovery-tools"]
}
```
