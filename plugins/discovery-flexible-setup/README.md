# discovery-flexible-setup

Set up a Microsoft Discovery project, or re-plan one when new information
arrives. `discovery-project-setup` is the entry point: it gathers the current
state, writes a proposal, waits for your approval, applies only the approved
changes, and commits a git ledger before and after. It never deletes; retired
items are marked instead.

It works two ways: **propose a full project** from material you supply (a grant
application, proposal, papers, datasets), or **walk through it** step by step.

| Step | Skill | What it does |
|---|---|---|
| Entry point | [`discovery-project-setup`](skills/discovery-project-setup) | Routes each step below; owns the proposal format, apply, git-ledger, and retirement rules |
| 1. Purpose | `discovery-project-setup` | Drafts the Purpose, citing the documents you supplied |
| 2. Data intake | [`discovery-curate-bookshelf`](skills/discovery-curate-bookshelf) | Places each source: Bookshelf shelf, `data/`, or `data/external-sources.md` |
| 3. Outcomes | [`discovery-define-outcomes`](skills/discovery-define-outcomes) | 4–5 measurable outcomes, each with a type, evidence, source, and draft rubric |
| 3b. Pre-flight (optional) | [`discovery-clarify-project`](skills/discovery-clarify-project) | Up to 5 clarifying questions before work starts |
| 4. Tasks | [`discovery-work-breakdown`](skills/discovery-work-breakdown) | Task tree from Purpose, Outcomes, rubrics, and existing tasks: validation on every task, dependencies, suggested execution patterns, human-in-the-loop pauses before safety or cost risks, and compute plans |
| 4b. Investigation design (optional) | `discovery-design-tasks`, from [`discovery-cape-labs`](../discovery-cape-labs) | Detailed design of one milestone for a specific agent set, after agents are installed |
| 5. Agents, skills, tools | [`discovery-provision-tools`](skills/discovery-provision-tools) | Recommends and installs agents, skills, MCP servers, and A2A agents, with a trust review |
| 5, agents | [`discovery-add-agents`](skills/discovery-add-agents) | Lists and deploys agents from microsoft/discovery |
| 6. Assign and finalize | `discovery-project-setup` | Assigns agents to tasks and suggests next steps |

The four step skills also run on their own, reusing the entry point's
`references/`, so they ship together in this plugin.

```bash
copilot plugin marketplace add timothymeyers/discovery-tools
copilot plugin install discovery-flexible-setup@discovery-tools
copilot plugin install discovery-cape-labs@discovery-tools   # optional, step 4b
```

For other agent hosts, install skills one at a time with
`gh skill install timothymeyers/discovery-tools <skill>`, and install the
`discovery-project-setup` family together.
