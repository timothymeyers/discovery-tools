# general-science-labs

Science skills that work in any field.

| Skill | What it does |
|---|---|
| [`scientific-database-access`](skills/scientific-database-access) | Get an authorized, versioned route to a database before calling it inaccessible |
| [`scientific-evidence-qc`](skills/scientific-evidence-qc) | Append-only evidence layer with coverage and QC reports |
| [`osti-literature-search`](skills/osti-literature-search) | Search DOE literature on OSTI.GOV. Pointer skill: needs the external [`osti-axi`](../../tools/external-tools.json) CLI, installed separately |

```bash
copilot plugin marketplace add timothymeyers/discovery-tools
copilot plugin install general-science-labs@discovery-tools
```

For other agent hosts, install skills one at a time with
`gh skill install timothymeyers/discovery-tools <skill>`.
