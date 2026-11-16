# Counting evidence: laws, parameter rows, numeric measurements, publications

## Why one blended "record count" overstates coverage

A single kinetic-law entry from a source database commonly explodes into
many parameter rows: one law (one enzyme/organism/condition combination as
reported by the source) can yield separate rows for Km, kcat, Vmax, Ki, and
so on, sometimes further split by substrate or cofactor. A single
publication commonly backs many records across many laws. If a coverage
report reports only "N records," a reader has no way to tell whether N
reflects N independent experimental findings, or a much smaller number of
underlying laws/publications multiplied out by however many parameter types
each one happened to report.

**Required: report each of these as its own separate count**

1. **Laws** — distinct source-level law/entry identifiers (the source's own
   primary key for one reported experiment/condition, before parameter-type
   fan-out). Two parameter rows sharing the same law identifier are the same
   underlying experiment, not two independent ones.
2. **Parameter rows** — total observation records after fan-out (one row per
   observation, per this skill's core principle #1). This is the number
   the schema's "one row per observation" design produces, and it will
   normally be larger than the law count.
3. **Numeric measurements** — the subset of parameter rows where the value
   is actually `present` with a real numeric `normalized_value` (i.e.
   excludes `unknown`/`not_applicable`/`unavailable_source`/
   `deferred_resolution` rows, which are explicit gaps, not measurements).
   Report explicit-gap counts (by missingness state) alongside this, not
   folded into it.
4. **Publications** — distinct citation identifiers (e.g. PubMed ID or DOI)
   referenced across all records, with a separate note for how many are
   cited by *more than one* record (a linked group — see below) and how many
   are cited by more than one source (mirrored/shared evidence across
   databases).

**Record volume within any one of these categories is not independent
experimental evidence.** A reaction with 300 parameter rows backed by 3
publications and 12 underlying laws has roughly 12 independent experimental
data points, not 300 — a coverage report that only surfaces "300 records for
this reaction" invites the reader to overweight it.

## Publication linkage: group, don't deduplicate and don't over-count

When multiple records cite the same publication ID:

- Surface them as one **linked group** in the report (e.g. "publication X is
  cited by records A, B, C — same underlying paper, N reported
  measurements") so a reviewer can see the shared dependency at a glance.
- Do **not** automatically merge/deduplicate those records into a single row
  — they may still represent genuinely distinct measurements (different
  parameter types, conditions, or organisms) reported in the same paper.
- Do **not** count them as N fully-independent pieces of evidence either —
  note the shared-publication dependency explicitly in any summary that
  claims "evidence from N sources/publications."
- Separately flag publications cited by records from **more than one source
  database** (the same paper mirrored/re-curated into two different
  databases) — this is a distinct and rarer situation from ordinary
  multi-record citation within one source, and conflating them hides
  genuine cross-database mirroring.

## Worked shape (illustrative numbers only — not a general rule)

A source-level page might report, say, 95 law entries for one EC number;
after parameter-type fan-out those 95 laws could produce several hundred
parameter rows. Reporting "several hundred records for this EC number"
without also stating the ~95 underlying laws and the (likely much smaller)
count of distinct publications materially overstates how much independent
evidence exists. Always carry all three (and the publication count) side by
side in the coverage output. Any concrete numbers from one project's real
corpus belong in that project's fixtures (see
`fixtures/project-example/`), not asserted here as general facts.
