# Identity-confirmation checks

A crosswalk entry claims that two identifiers (across e.g. `rhea` and
`model_native`, or two ChEBI IDs, or two UniProt accessions) refer to the
**same** underlying reaction/compound/enzyme. That claim needs actual
independent evidence. Two shortcuts are explicitly banned as sole proof:

## 1. Name/label similarity is never sufficient alone

Reaction and compound names are inconsistent across databases (synonyms,
transliteration, trivial-name vs. systematic-name, organism-specific
naming conventions). A name match is a *lead to check*, never a
confirmed identity. If the only evidence for a mapping is that two names
look similar, record the mapping as `state: unknown` (or omit it),
not `state: present`.

## 2. A shared EC number is never sufficient alone

EC numbers classify catalytic function, not reaction/protein identity:

- Multiple **paralogous proteins** in the same organism can share one EC
  number (isoenzyme case — see `references/ambiguity-taxonomy.md`).
- Multiple **distinct reactions** (different specific substrates within a
  general class) can share a broader EC number.
- A **generic/partial EC** (e.g. `1.2.1.-`) explicitly means the specific
  assignment is itself unresolved upstream — treating it as a precise
  match compounds the ambiguity rather than resolving it.

`method: ec_number_match` exists in the schema specifically so this weak
evidence type is visible and auditable, never silently promoted to
`confidence: high`.

## 3. What IS sufficient

- `direct_xref_file`: the source's own published cross-reference table
  (Rhea's `rhea2xrefs.tsv`, MetaNetX's `reac_xref.tsv`/`chem_xref.tsv`).
- `rest_api_lookup` / `bulk_export_lookup`: a live/bulk retrieval that
  itself returns the target-namespace ID as an attribute of the queried
  record (not inferred by the caller).
- `smiles_structure_match` is a *secondary* corroborating check only —
  useful to confirm a candidate found some other way, never the sole
  basis for a `high`-confidence mapping.

`scripts/build_crosswalk.py`'s `_check_identity_strength` flags (as a
build-time warning, not a silent pass) any entry whose only `present`
cross-source evidence, across every non-`model_native` namespace, is
`ec_number_match` and/or `smiles_structure_match`. That entry should be
treated as identity-unconfirmed pending a stronger source.

## 4. Mass/charge balance is a genuine cross-check only if independent

`scripts/validate_crosswalk.py` sums atom counts (by element) and net
charge across substrates vs. products, using formulas/charges pulled from
an **independently retrieved** ChEBI properties table (never derived from
the reaction equation being checked, and never derived from the
crosswalk record itself). If the two numbers were sourced from the same
place, an "imbalance" could never actually be detected — it would just
reproduce whatever the equation already implied. Keep the ChEBI
properties file (`fixtures/expected/synthetic-chebi-properties.json` in
this skill; `data/chebi-compound-properties.json` in the project) as a
separate artifact from the crosswalk data file for this reason.

## 5. A duplicate ChEBI ID across two "distinct" entries is a red flag

If two entries with `entity_type: compound` both claim `state: present`
for the same ChEBI ID, and neither is flagged `generic_compound.applies:
true`, that is very likely an accidental merge of two things that should
be one entry (or a genuine duplicate that should be de-duplicated), not
two legitimately distinct species. `check_chebi_identity_collisions` in
the validator reports this as an error, not a warning.
