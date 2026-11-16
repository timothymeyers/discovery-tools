# Strain vs. Species in BRENDA's Organism Field

BRENDA's bulk-JSON `protein.organism` field is **species-level free text
only** (e.g. `"Escherichia coli"`, `"Prochlorococcus marinus"`) — it never
encodes a strain designator, NCBI taxonomy ID, or ecotype.

## What this skill does

- `extract_subset.py` matches your `--organism-substring` against this
  field with an exact, case-sensitive substring check — never inferred,
  never fuzzy-matched.
- `build_canonical_records.py` emits every matched record's
  `organism_species` exactly as BRENDA wrote it, plus a `strain_note`
  stating explicitly that no strain is asserted or inferred beyond BRENDA's
  own field.
- If a kinetics observation's free-text `comment` field happens to name a
  specific strain (a real, observed pattern — e.g. `"wild-type strain
  K-10"`), that text is captured verbatim in
  `strain_hints_from_comments` — labeled as an **incidental hint from
  BRENDA's own free text**, never promoted to a canonical strain field and
  never asserted to match whatever strain you actually care about.

## Why this matters

Two independent extraction passes over the same BRENDA release found real
protein entries at the species level with **no strain confirmation** for
either of two different target strains (a cyanobacterium ecotype and *E.
coli* K-12 MG1655) — in both cases, the correct, honest record is "BRENDA
has this species, strain unconfirmed", not a silently-assumed strain match.
Downstream consumers of this skill's output must apply their own
strain-resolution logic (e.g. cross-referencing a UniProt accession, when
BRENDA's own `accessions` field on the protein entry happens to be
populated) rather than treating a species-level BRENDA hit as
strain-specific evidence.
