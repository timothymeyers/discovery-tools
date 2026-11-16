# pLDDT / PAE quality triage

Keep this logic strictly separate from oligomer/complex-flag logic (see
`oligomer_complex_flags.md`). This file covers **only** per-residue local
confidence in the modeled chain.

## What pLDDT and PAE actually measure

- **pLDDT** (predicted Local Distance Difference Test, 0–100 per residue):
  AlphaFold's confidence in the local backbone geometry around that residue.
  It is a per-residue, single-chain quantity.
- **PAE** (Predicted Aligned Error): a full pairwise residue×residue matrix
  describing the model's confidence in the relative position/orientation of
  residue pairs. Useful for judging confidence in relative domain
  positioning within a single modeled chain (or, for AlphaFold-Multimer
  predictions specifically, inter-chain positioning — see the note at the
  end of this file).

Neither pLDDT nor PAE from an AlphaFold-**Monomer** prediction says anything
about whether the true biological unit is a monomer or part of a larger
complex.

## Standard AlphaFold confidence bands

(Per AlphaFold DB's own published bands; Jumper et al. 2021 *Nature*;
Tunyasuvunakool et al. 2021 *Nature*.)

| Band | pLDDT range |
|---|---|
| Very high | > 90 |
| Confident | 70–90 |
| Low | 50–70 |
| Very low | ≤ 50 |

"Confident-or-better" (pLDDT > 70) is a common summary statistic — the
fraction of modeled residues at or above 70 — used to drive a coarse
usability decision.

## Example configurable triage thresholds

These are illustrative starting points, not fixed constants — always make
them configurable in code and re-justify them for your own use case:

- ≥ 80% confident-or-better → structure usable as-is for downstream
  geometric/hydrodynamic inference.
- 50–80% → usable for sensitivity analysis / ranking context only, not as a
  validated final structure.
- 30–50% → flag for manual structural review before use.
- < 30% → too unreliable for geometric/hydrodynamic use; exclude.

## Low-confidence segment detection

For a finer-grained view than the bulk fraction, locate contiguous runs of
low-confidence residues (e.g. pLDDT ≤ 50, minimum run length 3) with exact
start–end residue numbers. These often correspond to intrinsically
disordered regions or termini and are useful context even when the bulk
confident-or-better fraction looks acceptable.

## Sequence coverage

If the modeled region (`sequenceEnd` from the AlphaFold API) does not cover
the full UniProt sequence length, or if the entry is a multi-fragment model
(fragment number > 1), treat the coverage gap as an independent trigger that
forces a review-level outcome regardless of the pLDDT band — a partial
structure cannot represent the full assembled diffusing/functional unit.

## PAE availability caveat

PAE files are large (megabytes per protein) — do not bulk-fetch them for
every candidate in a large set by default; fetch on demand for a shortlist
that needs the finer-grained pairwise confidence view, and always record
`pae_available` explicitly (true/false) rather than leaving it implicit when
omitted for cost reasons.

## Do not conflate with assembly logic

Never let a complex/oligomer flag change which pLDDT band a residue or
protein falls into, and never compute the confident-or-better fraction
differently depending on the complex flag. Keep the two decision axes
entirely separate; see `oligomer_complex_flags.md` for how to combine their
*outputs* without conflating their *logic*.
