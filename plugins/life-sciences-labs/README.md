# life-sciences-labs

Life-sciences data skills.

| Skill | What it does |
|---|---|
| [`sabio-rk-kinetics`](skills/sabio-rk-kinetics) | SABIO-RK kinetic laws with organism, genus, and EC tiering |
| [`brenda-enzyme-data`](skills/brenda-enzyme-data) | Enzyme data from a caller-supplied, licensed BRENDA download |
| [`biocyc-network-context`](skills/biocyc-network-context) | BioCyc/EcoCyc/CyanoCyc reaction → enzyme → gene → pathway |
| [`alphafold-structure-provenance`](skills/alphafold-structure-provenance) | UniProt lookup and version-aware AlphaFold DB structures |
| [`biochemical-identifier-crosswalk`](skills/biochemical-identifier-crosswalk) | Rhea / ChEBI / MetaNetX / UniProt identifier crosswalk |

```bash
copilot plugin marketplace add timothymeyers/discovery-tools
copilot plugin install life-sciences-labs@discovery-tools
```

For other agent hosts, install skills one at a time with
`gh skill install timothymeyers/discovery-tools <skill>`.

Pairs well with [`general-science-labs`](../general-science-labs) for database access and evidence QC.
