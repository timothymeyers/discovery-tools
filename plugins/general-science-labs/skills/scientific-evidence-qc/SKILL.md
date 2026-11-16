---
name: scientific-evidence-qc
description: |
  Cross-domain / Data quality and reproducibility — Produce an auditable,
  append-only observation layer and coverage/QC report over merged
  scientific-database evidence (kinetics, reaction stoichiometry, identifiers,
  provenance). WHEN a user says "normalize scientific observations", "merge
  database evidence", "audit provenance", "coverage and missingness report",
  "validate units", "QC the evidence layer", "is this traceability check
  real", "how many kinetic laws vs parameter rows", or asks you to
  build/extend a cross-source QC script over reaction-evidence-schema-shaped
  records, use this skill before writing new QC invariants, before trusting an
  existing "N/N passed" QC summary, and before treating a schema-conformance
  or requirement audit as sufficient evidence that the QC logic itself is
  correct.
metadata:
  version: "1"
  category: "Cross-domain"
  subfield: "Data quality and reproducibility"
  secondary: "Life sciences (examples use reaction evidence)"
---

# Scientific Evidence QC

## Purpose

Merging observations from multiple scientific databases (SABIO-RK, BRENDA,
BioCyc/CyanoCyc, Rhea, ChEBI, UniProt, MetaNetX, BioModels, model-native
curation) into one evidence layer is not just schema validation. A QC script
that only checks "does every record conform to the schema" or "does the
stated requirement have a corresponding check" can still be **wrong** —
it can enshrine a bug as an invariant, silently drop unsupported unit
conversions, or claim "traceable" when it only checked that a record's
*identity* (not its *numbers*) matches the raw source.

**Anti-pattern this skill exists to prevent:** treating a passing
requirement-audit ("every bullet in the spec has a matching check function")
as proof the QC logic is correct. A requirement audit only proves coverage of
*stated* requirements — it cannot catch a check that encodes the wrong
answer. Only a **known-answer test** (a check exercised against data whose
correct outcome you already know, including a case that should trigger
failure) can catch that. See `references/known-answer-and-mutation-tests.md`
for the canonical worked example (DX-36) of a QC invariant that passed every
requirement audit while actively rewarding a real ingestion bug.

## Core design principles (reuse across any source/domain)

1. **One row per observation, never one row per reaction/entity.** The same
   reaction, compound, or enzyme legitimately produces many records — from
   different sources, conditions, organisms, or evidence types. Do not
   collapse or deduplicate at ingestion time; that is separate reconciliation
   work with its own ambiguity-preserving rules (see the
   `biochemical-identifier-crosswalk` skill).
2. **Raw and normalized values are both retained, always.** Every scalar
   field carries `raw_value` + `normalized_value` (+ units in both forms) so
   normalization never silently overwrites what the source actually said.
3. **Missingness uses a fixed six-value vocabulary, not `null`-means-
   everything:** `present`, `unknown`, `not_applicable`, `imputed`,
   `unavailable_source`, `deferred_resolution`. See
   `references/missingness-and-access-vocabulary.md` for the full
   definitions and worked examples of picking the right one.
4. **Access outcome is a separate axis from observation missingness.** A
   source being reachable/unreachable, a query succeeding/failing/timing out,
   and a specific field being populated/absent are three different facts.
   Collapsing "the API returned an error" and "the API answered and the
   value doesn't exist" into the same state destroys the ability to retry
   the right thing later. See
   `references/missingness-and-access-vocabulary.md`.
5. **Unsupported unit conversions are reported as warnings, never silently
   omitted.** A fixed allow-list of `(unit_raw, unit_normalized)` factors is
   a reasonable starting point, but a pair outside that list must surface as
   an explicit "unchecked — not in factor table" warning finding, not vanish
   from the report. Silence here reads as "verified fine" to a downstream
   consumer when it actually means "never checked." See
   `references/unit-conversion-and-traceability.md`.
6. **Traceability/reproducibility checks must replay the numbers, not just
   identity.** Checking that a normalized record's PMID, organism name, or
   frame ID appears in the cached raw response proves the record points at
   *a* real source entry — it does not prove the numeric value, unit,
   ligand linkage, or condition was carried over correctly. A traceability
   sample that stops at identity-matching will pass even when the actual
   number was transcribed wrong. See
   `references/unit-conversion-and-traceability.md` for what a real numeric
   replay sample looks like.
7. **Report separate counts for laws, parameter rows, numeric measurements,
   and publications** — never one blended "record count." A single kinetic
   law entry commonly explodes into many parameter rows (one per
   Km/kcat/Vmax/condition); a single publication commonly backs many
   records. Record volume in any one of these categories is not independent
   experimental evidence, and conflating them overstates coverage. See
   `references/coverage-counting.md`.
8. **Known-answer tests, including at least one mutation test per
   consequential check, are REQUIRED — not optional polish.** A
   check-by-check requirement audit (“every listed requirement has a
   corresponding function”) is necessary but not sufficient: it cannot tell
   you the function computes the *right* answer. Pair every check that
   encodes a domain invariant with (a) a known-answer assertion against data
   whose correct classification you can independently verify, and (b) a
   mutation test that reintroduces the specific wrong outcome a plausible
   bug would produce, and asserts the check now fails. See
   `references/known-answer-and-mutation-tests.md`.
9. **Publication linkage groups, never silently deduplicates.** Multiple
   records citing the same PubMed ID should be surfaced as one linked group
   (so a reviewer can see "these N rows all come from one paper"), not
   auto-merged into a single row and not counted as N independent pieces of
   evidence.

## Procedure for building or extending a QC pass

1. Read the canonical schema the evidence layer must conform to (e.g.
   `docs/schemas/reaction-evidence.schema.json` and its `README.md`) before
   writing any check — the six-value missingness enum and per-block required
   fields are defined there, not in this skill.
2. For every check you add, write it as a **general, source-agnostic
   invariant** first. If the check only makes sense for one project's
   specific organisms, taxids, gap-fill identifiers, or tier assignments,
   it does not belong in the shared check function — put the project-specific
   assertion in `fixtures/` instead (see
   `references/known-answer-and-mutation-tests.md` §"Where project-specific
   assertions belong" and `fixtures/project-example/`).
3. For every check that encodes "the correct classification of X is Y" (not
   just "the record parses"), add a known-answer assertion against
   independently-verifiable data, plus a mutation test that proves the check
   would have failed under the specific wrong-outcome bug it exists to catch.
4. Keep access-outcome tracking (`unavailable_source` vs. a genuinely
   consulted-and-absent `unknown`) as first-class output, separate from the
   missingness of any individual field.
5. When a unit pair, source-record shape, or traceability path isn't
   supported yet, emit an explicit warning finding — never a silent skip.
6. Report coverage counts split by category (laws / parameter rows / numeric
   measurements / publications / explicit gaps), not a single blended count.
7. Before trusting an existing "N pass / 0 warning / 0 failure" summary,
   re-derive at least one known-answer case yourself. A requirement audit
   that every check exists is not evidence every check is correct — see the
   DX-36 worked example.

## Reusable assets

- `references/missingness-and-access-vocabulary.md` — the six-value
  missingness enum plus the separate access-outcome vocabulary, with
  worked "which state do I pick" examples.
- `references/known-answer-and-mutation-tests.md` — why requirement audits
  are insufficient, the required known-answer + mutation-test pattern, and
  the DX-36 SABIO-RK taxonomy-lineage worked example in full (bug, the
  invariant that hid it, the fix, and the mutation test that now catches
  it). Also states where project-specific assertions belong.
- `references/unit-conversion-and-traceability.md` — warn-not-silent-skip
  for unsupported unit conversions, and what a genuine numeric-replay
  traceability sample checks versus an identity-only sample.
- `references/coverage-counting.md` — the four required separate count
  categories (laws, parameter rows, numeric measurements, publications) with
  a worked example of why blending them overstates coverage.
- `scripts/coverage_counts.py` — small, dependency-light, source-agnostic
  helper that computes the separated law/parameter-row/numeric-measurement/
  publication counts from a list of reaction-evidence-schema-shaped records.
  Runnable standalone against its own synthetic demo data.
- `scripts/unsupported_unit_warnings.py` — small helper that scans a corpus
  for `(unit_raw, unit_normalized)` pairs missing from a supplied factor
  table and returns them as warning findings instead of dropping them.
- `fixtures/synthetic_corpus_sample.json` — a small, fully synthetic
  (fabricated, not real evidence) set of reaction-evidence-schema records
  covering laws/parameter-rows/publications fan-out, used by the scripts'
  self-tests and as a template for exercising new checks offline.
- `fixtures/project-example/` — where MED4/gap2/tier-4-specific (or any
  other single-project) known-answer assertions belong; illustrates the
  DX-31/DX-36 corpus as an *example* of a project fixture, explicitly marked
  as not a general rule.

## Scope boundaries

- This skill is about QC/coverage over an *already-normalized* evidence
  layer, not about ingestion adapters for a specific source (see the
  `scientific-database-access` skill for finding authorized access) or
  identifier reconciliation across sources (see the
  `biochemical-identifier-crosswalk` skill).
- Do not encode any single project's organism, taxid, gap-fill identifier,
  or tier-distribution expectation as a general invariant. Those are
  fixtures, not shared logic — a new project's valid exact-organism evidence
  must never fail a shared check merely because an earlier corpus only had
  broad fallback records.
- Never record secrets, credentials, absolute local file paths, or internal
  project/task identifiers in QC output, findings, or committed fixtures.
