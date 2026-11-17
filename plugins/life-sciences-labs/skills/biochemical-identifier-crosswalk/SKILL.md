---
name: biochemical-identifier-crosswalk
description: |
  Life sciences / Biochemistry — Reconcile reactions, compounds, proteins, and
  taxa across Rhea, ChEBI (via EBI OLS4), MetaNetX, and UniProt into one
  one-to-many identifier crosswalk without collapsing chemical or mapping
  ambiguity. Config-driven: entities are declarative YAML records, so adding a
  reaction, compound, or enzyme means adding a record rather than editing
  code. Resolves reaction direction from the retrieved equation text rather
  than the Rhea LR/RL label, verifies mass and net-charge balance against an
  independently retrieved ChEBI formula and charge table, and never treats
  name similarity or a shared EC number alone as proof of identity. WHEN: "map
  Rhea to ChEBI, UniProt, or MetaNetX", "identifier crosswalk", "reaction
  identifier reconciliation", "resolve reaction direction", "preserve
  isoenzyme ambiguity", "is this the same reaction or compound", "mass and
  charge balance check", "currency metabolite vs generic compound".
metadata:
  version: "1"
  category: "Life sciences"
  subfield: "Biochemistry / bioinformatics"
  secondary: "Chemistry (formula, charge and mass balance checks)"
---

# biochemical-identifier-crosswalk

Reconcile a reaction/compound/protein/taxon across model-native,
Rhea, ChEBI, EC, UniProt, NCBI Taxonomy, BioCyc/CyanoCyc, SABIO-RK, BRENDA
ligand, KEGG, and MetaNetX namespaces into one
`identifier-crosswalk.schema.json`-conformant record — while deliberately
preserving every ambiguity that a naive "one canonical ID per entity"
crosswalk would silently destroy.

## Dependencies

This skill is **not** standard-library only. `gh skill install` and plugin
install do not install Python packages, so install these before first use:

```bash
python3 -m pip install -r requirements.txt   # PyYAML, jsonschema
```

`scripts/build_crosswalk.py` needs `PyYAML` to read the declarative entity
configs; `scripts/validate_crosswalk.py` needs `jsonschema`.
`scripts/formula_parser.py` is standard-library only and works without either.

## When to use

Trigger when the user asks anything like:

- "Map this Rhea/ChEBI ID to UniProt / MetaNetX / KEGG"
- "Is this really the same reaction/compound as that one?"
- "Build an identifier crosswalk for these reactions"
- "Resolve which direction this reaction actually runs"
- "This reaction has isoenzymes / paralogs — keep them separate"
- "Check mass and charge balance for this reaction"
- "Is a locus tag the same thing as a reaction ID?" (no)

Do NOT trigger for:

- Retrieving raw kinetic parameters (`sabio-rk-kinetics`,
  `brenda-enzyme-data`) or gene→pathway network context
  (`biocyc-network-context`) — this skill reconciles *identifiers*, not
  parameter values or network structure. Those skills are natural
  upstream/downstream neighbors: crosswalk entries carry
  `evidence_ref`/`upstream_tasks` pointers into that other evidence,
  they don't replace it.
- General database access/authorization triage — see
  `scientific-database-access` first if a source's reachability itself
  is in question.

## Hard rules (enforced by the scripts, not just documented)

1. **Direction comes from the equation, never the LR/RL label.** Every
   reaction record must carry `direction_evidence.resolved_from:
   equation_text` plus the actual retrieved equation string.
   `scripts/build_crosswalk.py` refuses to build a reaction record
   missing this or attempting to justify direction any other way. Read
   `references/ambiguity-taxonomy.md#direction` before assuming an LR
   entry is "the forward direction" — it frequently is not.
2. **Mass/charge balance uses an independently retrieved ChEBI
   properties table**, never numbers derived from the reaction equation
   itself. `scripts/validate_crosswalk.py` sums per-element atom counts
   and net charge across substrates vs. products and reports any
   mismatch as an error.
3. **A real formula parser, not a formula regex.**
   `scripts/formula_parser.py` handles nested brackets and
   hydrate/adduct separators, validates every token against the real
   IUPAC element table, and *rejects* (rather than silently mis-parses)
   R-groups, wildcards, unbalanced brackets, and unknown symbols. See
   `references/formula-grammar.md` for the accept/reject table.
4. **Name similarity and shared EC numbers are never sole proof of
   identity.** `scripts/build_crosswalk.py` flags any entry whose only
   `present`-state cross-source evidence is
   `smiles_structure_match`/`ec_number_match` as identity-unconfirmed.
   See `references/identity-checks.md`.
5. **Five ambiguity dimensions stay explicit, never collapsed:**
   direction, protonation, compartment, generic-compound, isoenzyme (see
   `references/ambiguity-taxonomy.md`). Preserve one-to-many mappings
   (one Rhea master → up to 4 directional IDs; one EC class → multiple
   isoenzyme accessions) rather than picking a "canonical" one.
6. **`generic_compound` ambiguity ≠ currency-metabolite caveat.** A
   compound that is chemically underspecified (R-group, unresolved
   stereochemistry) is `generic_compound.applies: true`. A compound
   that is fully specified but universally shared (water, CO₂, H⁺, Pi,
   ATP/ADP, NAD(P)(H), CoA) is a `role_class: currency` participant —
   distinct, tracked separately, and never itself sufficient to confirm
   a reaction match. `scripts/build_crosswalk.py` requires every
   stoichiometry participant to declare `role_class:
   currency|generic|specific` and rejects configs that mix the two
   concepts up.
7. **A locus tag is a gene identifier, not a model-native reaction
   ID.** Reusing one for a reaction/enzyme's `model_native` mapping is
   fine (`method: locus_tag_reuse`) only when explicitly labeled as
   such in the record's `note` — never presented as though a real
   SBML/whole-cell-model reaction ID artifact exists when it does not.

## Workflow

1. **Resolve access before claiming a source is unavailable.** Check
   `references/sources.md` for the verified recipe/gating status per
   source (Rhea bulk mirror, ChEBI via OLS4 REST, MetaNetX bulk
   xref-only, UniProt REST, KEGG-via-Rhea-only, CyanoCyc/BRENDA
   SOAP/SABIO-RK confirmed gated as of their last verification date).
   Re-verify gating rather than assuming an old note is still current.
2. **Retrieve the actual equation text** for every reaction, not just
   its Rhea ID — you need it to resolve direction (rule 1 above) and to
   populate `stoichiometry` from the authoritative source, never from
   the entity's name.
3. **Author one YAML config record per entity** (reaction/compound/
   gene/enzyme) under a project-appropriate config path — see
   `fixtures/configs/*.yaml` for two complete worked examples (a
   non-Calvin-cycle reaction; an ambiguous isoenzyme pair). Do not write
   Python literals for new entities; that reintroduces the
   hand-assembled-builder problem this skill replaces.
4. **Build:**
   ```bash
   python3 scripts/build_crosswalk.py <config1.yaml> [<config2.yaml> ...] \
       -o <output.json> --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
   ```
   Fix any `ConfigError` (a hard rule violation) before proceeding;
   review any printed build-time warnings (identity-strength,
   currency-metabolite caveats) even though the build still succeeds.
5. **Validate:**
   ```bash
   python3 scripts/validate_crosswalk.py <output.json> \
       --schema <path-to-identifier-crosswalk.schema.json> \
       --chebi-props <path-to-an-independent-ChEBI-properties-file.json>
   ```
   This must exit 0 (schema conformance + mass/charge balance +
   EC-consistency + ChEBI-identity-collision + namespace-coverage
   report) before the crosswalk is considered usable.
6. **Report unresolved items as unresolved.** If a mapping/direction/
   isoenzyme assignment genuinely cannot be determined from the sources
   queried in this pass, record it as `unknown`/`unavailable_source`
   with a note explaining what was tried — never fill the gap with a
   guess "for completeness."

## Fixtures

- `fixtures/configs/synthetic-non-calvin-citrate-synthase.yaml` — a
  non-Calvin-cycle (TCA-cycle-style) reaction fixture, illustrating
  `direction_evidence`, the currency-metabolite `role_class`, and that
  `generic_compound` need not apply when every participant is fully
  specified. **Synthetic**: every identifier is a deliberately
  out-of-range placeholder (`RHEA:990010`, `CHEBI:99000x`) — never treat
  these as real cross-references.
- `fixtures/configs/synthetic-ambiguous-isoenzyme-pfk.yaml` — a
  two-paralog isoenzyme fixture (same shape as the project's already
  -verified GAPDH gap1/gap2 case), illustrating `ambiguity.isoenzyme`,
  `alternatives`, and the identity-strength warning for
  `ec_number_match`-only evidence. **Synthetic placeholder** pending a
  real PFK isoenzyme case from workstream 3 — swap this fixture for that
  evidence once it lands, per the `TODO(workstream-3)` note in the file.
- `fixtures/expected/synthetic-fixtures-built.json` +
  `fixtures/expected/synthetic-chebi-properties.json` — the verified
  output of building the two configs above and validating them
  end-to-end (0 schema errors, 0 balance errors) — a regression baseline
  for anyone editing the scripts.

## Do not generalize

- The GAPDH gap1/gap2 isoenzyme ambiguity and the RuBisCO/PGK
  direction-reversal examples above are real, project-verified findings
  for *Prochlorococcus marinus* MED4 — cited here as worked illustrations
  of the *general* rules (read the equation; don't guess isoenzymes),
  not as claims that apply to other organisms or reactions.
- A source confirmed `unavailable_source` on a past `retrieved_at` date
  is not permanent — re-check before repeating that conclusion in a new
  task.
