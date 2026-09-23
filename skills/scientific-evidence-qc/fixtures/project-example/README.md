# Project-example fixture: where project-specific QC assertions belong

This directory illustrates the rule from `references/known-answer-and-
mutation-tests.md` §"Where project-specific assertions belong": **any single
project's organism names, taxonomic IDs, gap-fill identifiers, or expected
tier/coverage distributions are fixtures, not general invariants.** They
must never be hardcoded into a shared QC check function as if they were
universal biochemical facts.

## The illustrative example (this repository's own corpus)

This repository's own DX-31/DX-36 evidence layer is the worked example
referenced throughout this skill. Its project-specific facts — which are
**not** general rules, only true of *this* project's specific corpus at a
specific point in time — include things like:

- A specific target-organism NCBI taxonomy ID that must never appear as a
  claimed match in a source whose corpus contains no records for that
  organism (a "leak" check specific to one project's exclusion list).
- A specific documented set of organism names known (from independent
  inspection of the raw source pages) to belong to a target lineage, used as
  a known-answer set for one project's tier-classification check.
- A specific isoenzyme gap-fill identifier that one project's BioCyc/CyanoCyc
  network-context ingestion must resolve to.
- The specific before/after record and tier-distribution counts produced by
  one ingestion bug fix (see `docs/database-evidence/dx31-qc-methods.md`'s
  "DX-36 correction" section in the main repository for the actual numbers).

**How to reference this correctly in a shared check:** the general,
source-agnostic check (e.g. "tier/organism consistency," see
`references/known-answer-and-mutation-tests.md`) takes the known-answer set,
excluded taxid, or expected identifier as a **parameter** — read from a
fixture file such as this one — rather than embedding it as a literal in the
shared script. That way, a different project pointing this skill at a
different organism, source, or corpus reuses the same check logic with its
own fixture, and one project's fixture data can never cause a false failure
on another project's legitimately different (and correctly classified)
evidence.

## What to put in a new project's fixture

When applying this skill to a new evidence-QC pass, create a sibling
directory (e.g. `fixtures/<your-project>/`) containing:

- The known-answer set(s) your project's classification checks need,
  labeled with their source of truth (raw page inspection, external
  reference database, hand-verified computation) and retrieval/verification
  date.
- Any project-specific excluded/target identifiers (organism taxids, gene
  IDs, gap-fill identifiers) your invariants reference.
- A short note on scope: which corpus, which ingestion pass, and which date
  this fixture is valid for, so it is not silently reused as if it were a
  durable cross-project fact.

Do not put secrets, credentials, absolute local file paths, or internal
task/ticket identifiers in any fixture.
