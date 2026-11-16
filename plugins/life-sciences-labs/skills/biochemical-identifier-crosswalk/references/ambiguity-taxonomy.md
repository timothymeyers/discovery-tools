# Ambiguity taxonomy

Five distinct ambiguity dimensions are tracked per crosswalk entry
(`ambiguity.{direction,protonation,compartment,generic_compound,isoenzyme}`
in `identifier-crosswalk.schema.json`). Do not conflate them, and do not
collapse a genuinely unresolved dimension into a single guessed value.

## Direction

Rhea publishes four IDs per reaction: an undirected master (`UN`), a
left-to-right (`LR`), a right-to-left (`RL`), and a bidirectional (`BI`)
entry. **`LR`/`RL` is an internal ChEBI-ID-ordering convention Rhea uses to
generate the pair — it is not a claim about which direction is
biologically forward.** The only way to know which direction is
biological is to read the retrieved equation text itself and compare it
against how the reaction is conventionally described in the relevant
pathway/EC literature.

Concrete example already verified in this project (DX-27): RuBisCO's Rhea
`LR` entry is written as the *decarboxylation* direction (2×3PG → RuBP +
CO₂ + H₂O) — the reverse of the biological carboxylase reaction most
sources describe. The biological direction corresponds to Rhea's `RL`.
Phosphoglycerate kinase shows the same pattern the other way (its
Calvin-cycle direction, ATP-consuming, is the reverse of how PGK is
usually described in glycolysis).

**Rule enforced by `scripts/build_crosswalk.py`:** every reaction record
must carry `direction_evidence.resolved_from: equation_text` plus the
actual `equation_text`. A config that omits this, or sets
`resolved_from` to anything else (e.g. an attempt to justify direction
from the LR/RL suffix), is a rejected build, not a warning.

Retain **all** directional IDs for a reaction (`UN`/`LR`/`RL`/`BI`) rather
than picking one — this is the mapping-cardinality point below.

## Protonation

Charged metabolites (ATP⁴⁻, ADP³⁻, NADP³⁻, 3-PG³⁻, H⁺, ...) have sibling
ChEBI terms for adjacent protonation states, linked by ChEBI's own
`is_deprotonated_form_of`/`is_protonated_form_of` relations. Use the exact
charge-state ChEBI ID that appears in the source's own equation text, and
record the sibling ID as an explicit `alternatives` entry — never silently
normalize to "the" neutral or "the" charged form.

## Compartment

Rhea/ChEBI reaction and compound definitions are compartment-agnostic
(aqueous-phase only, no organelle/compartment tag). **A missing
compartment tag is not evidence that compartment is inapplicable** — it
usually means no compartmentalized model artifact exists yet to assert
one. Default new entries to `compartment.applies: true`,
`state: unknown`, not `not_applicable`, unless a project model explicitly
asserts (and cites) a compartment.

## Generic compound vs. currency metabolite — two different concepts

These are easy to conflate and must not be:

- **`generic_compound` (ambiguity flag, `applies: true` when relevant):**
  the compound itself is chemically **underspecified** — an R-group
  substituent, unresolved stereochemistry, a wildcard position, or a
  reaction written against a *class* of related compounds rather than one
  ChEBI entity. This is a structural-identity ambiguity.
- **`currency` metabolite (build-time `role_class`, not a schema
  ambiguity flag):** the compound is chemically **fully specified** (a
  single, unambiguous ChEBI ID) but is shared across enormous numbers of
  unrelated reactions — water, CO₂, H⁺, Pi, ATP/ADP, NAD(P)(H), CoA. There
  is nothing ambiguous about *what* the molecule is. The caveat is about
  **reaction-identity strength**: two reactions that only share currency
  metabolites as participants are not thereby shown to be the same
  reaction, and a currency metabolite alone should never be treated as
  confirming a reaction/pathway match.

`scripts/build_crosswalk.py` requires every stoichiometry participant to
declare `role_class: currency | generic | specific`, and rejects a config
where a `generic` participant's parent entry does not also set
`ambiguity.generic_compound.applies: true` — so the two concepts cannot
silently merge into one flag.

## Isoenzyme

When multiple protein accessions carry the same (possibly generic) EC
number and no in-scope source resolves which one corresponds to the
model-relevant activity, keep **all** candidates as separate entries with
`ambiguity.isoenzyme.applies: true` and cross-reference each other via
`alternatives`. Do not pick the "most likely" one from general biological
reasoning about the organism/pathway — that is exactly the kind of
project-specific conclusion this skill must not silently generalize.
Verified example: DX-27's GAPDH paralogs (gap1/gap2 in *P. marinus* MED4)
both carry only the generic EC `1.2.1.-`; neither UniProt's automatic
annotation nor any other queried source resolved which one is the
NADP-dependent activity, so both remain `unknown`.

## Mapping cardinality

Preserve one-to-many mappings; do not collapse them to satisfy a "one
canonical ID per entity" instinct:

- One Rhea master reaction → up to 4 directional IDs (`UN`/`LR`/`RL`/`BI`).
- One (possibly generic) EC class → multiple isoenzyme UniProt accessions.
- One compound → multiple protonation-state ChEBI siblings.

## Locus tag ≠ model-native reaction ID

A genome locus tag (e.g. `PMM0550`) identifies a **gene**, not a
**reaction**. Reusing a locus tag as the closest available project-native
identifier for a reaction/enzyme entry (`method: locus_tag_reuse`) is
legitimate *only* when explicitly labeled as such — it must never be
presented as though it were a real model-native reaction/species ID from
an SBML or whole-cell-model artifact. If no such artifact exists in the
project, say so in the `note`, and keep `model_native.state` as
`present` with `method: locus_tag_reuse` (a real value, correctly
labeled) rather than fabricating a reaction ID that does not exist.
