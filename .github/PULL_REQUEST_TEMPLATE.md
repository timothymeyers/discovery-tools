## What changed

<!-- Brief summary. If adding a skill, name it. -->

## Checklist

- [ ] `python3 .github/scripts/validate.py` passes
- [ ] Skill directory name matches the frontmatter `name`
- [ ] No hardcoded install paths (`~/.copilot/skills/...`, `/Users/...`)
- [ ] No script writes into its own install directory
- [ ] No generated artifacts committed (`token_usage_mined.json`, reports)
- [ ] `.github/plugin/marketplace.json` and `.claude-plugin/marketplace.json` are identical
- [ ] Version fields bumped together, if this is a release
