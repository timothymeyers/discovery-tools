# Unit conversion warnings and real numeric traceability

## Unsupported unit conversions must warn, never silently omit

A unit-conversion check typically works from a fixed allow-list of
`(unit_raw, unit_normalized)` pairs with a known linear scale factor (e.g.
mM→M, µg/ml→mg/ml). For every pair *in* the table, recompute
`raw_value * factor` and compare against `normalized_value` within a small
relative tolerance (enough to allow benign floating-point rounding, not
enough to mask a real conversion bug — 1e-3 relative is a reasonable
default).

**The failure mode to avoid:** when a `(unit_raw, unit_normalized)` pair is
*not* in the table, the naive implementation just skips it — no pass, no
warning, no entry in the findings list at all. To a report reader, "0
findings for this pair" is indistinguishable from "checked and fine." That
silence is a false all-clear.

**Required behavior:** any pair encountered in the corpus that has no known
factor must produce an explicit `warning`-severity finding —
"unit conversion unchecked: `(unit_raw, unit_normalized)` not in factor
table" — naming the pair and how many records it affects. This keeps the
allow-list-based approach (deliberately not guessing at unfamiliar unit
semantics) while making the *coverage gap itself* visible instead of
invisible. Extend the factor table when a new pair becomes common; until
then, the warning is the correct signal, not a skip.

## What "traceability" must actually check

A raw-to-normalized traceability sample exists to answer: "if I re-derive
this normalized record from the cached raw source response, do I get the
same thing?" There are two very different bars for "the same thing":

- **Identity-only bar (insufficient on its own):** the normalized record's
  publication ID matches the raw entry's publication ID; the organism name
  string matches; a frame ID string appears verbatim in the raw document;
  an EC number resolves into the raw extract. These checks prove the
  normalized record points at *a real, correctly-identified* source entry.
  They do **not** prove any numeric field, unit, ligand linkage, or
  condition was carried over correctly — a record could pass every one of
  these identity checks while its `parameter_value.normalized_value` is
  wrong, its unit was swapped, or its assay condition was dropped.
- **Numeric-replay bar (required for a real traceability claim):** re-parse
  the specific numeric value(s), unit(s), ligand/cofactor linkage, and
  condition fields from the *cached raw* response for the sampled record,
  and compare them — value-for-value — against what ended up in the
  normalized record. A sample is only a genuine reproducibility check once
  it does this; an identity-only sample should be labeled and reported as
  exactly that ("N/M records point at a resolvable, correctly-identified
  raw source entry") rather than described as "traceability" or
  "reproducibility" in a coverage report, since that language implies the
  numbers themselves were checked.

**Practical guidance:** when writing or reviewing a traceability sampler,
check what it actually re-derives. If it only re-derives an identifier or a
name string, either upgrade it to re-derive the numeric value(s) too, or
rename the finding so it doesn't overstate what was verified. Report both
bars separately if you have both (e.g. "N/M identity-resolvable; K/M with
numeric value independently reproduced") rather than merging them into one
misleadingly-labeled count.

## Sampling discipline

- Use a fixed, recorded random seed so the sample is reproducible across
  runs and reviewers can re-derive exactly which records were checked.
- Sample independently per source (not one pooled sample across sources with
  very different record counts), so a source with few records isn't
  effectively skipped.
- Record *why* a sampled record failed to reproduce (unparseable source
  identifier, raw entry not found, numeric mismatch, unit mismatch) rather
  than a bare pass/fail — this is what makes the finding actionable.
