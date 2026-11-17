# Oligomer / complex assembly flags

Keep this logic strictly separate from pLDDT/PAE quality triage (see
`plddt_pae_triage.md`). This file covers **only** whether the true
biological unit is a monomer, homo-oligomer, or hetero-complex.

## Why this is a separate axis

An AlphaFold-Monomer prediction models a single chain in isolation. Its
pLDDT and PAE describe confidence in that single chain's geometry — they say
**nothing** about whether the protein's actual functional/biological unit is
that isolated monomer or a larger oligomeric/hetero-complex assembly. A
protein can have an excellent monomer pLDDT and still be functionally and
physically part of a multi-subunit complex whose true diffusing/functional
size is much larger than the single modeled chain.

## Recommended source for the flag

A `cc_subunit` ("subunit structure") annotation from UniProt is a common,
authoritative source for this flag: proteins annotated as functioning in
multi-subunit complexes (e.g. polymerase subunits, multi-subunit
transporters/gyrases/synthetases, or any annotation describing hetero- or
homo-oligomeric assembly) should be flagged `complex_flag = Y` (or
equivalent), independent of any structural confidence metric.

## Recommended combination pattern

When combining the two independent axes into a final decision:

1. Compute the pLDDT/PAE-based base decision entirely on its own (see
   `plddt_pae_triage.md`).
2. Compute the complex/oligomer flag entirely on its own (from annotation
   data, not from AlphaFold confidence scores).
3. When combining, **cap or annotate, never silently override**: e.g. a
   `complex_flag = Y` protein should never reach an unqualified "usable
   as-is" outcome even with excellent monomer pLDDT — cap it at a
   "usable-with-caveats" tier instead, and attach an explicit caveat
   explaining that a downstream geometric/hydrodynamic quantity computed
   from the isolated monomer chain (e.g. a diffusion coefficient) is
   expected to misrepresent the true oligomeric species (commonly an
   overestimate of diffusion rate, since the true assembled complex is
   larger than the modeled monomer).
4. Keep the reason strings from each axis separate and both visible in the
   output, so a reviewer can tell whether a given outcome was driven by low
   pLDDT, by the complex flag, by both, or by neither.

## What NOT to do

- Do not let a `complex_flag = Y` change the computed pLDDT band or
  confident-or-better fraction.
- Do not let a high pLDDT "clear" or suppress the complex-flag caveat — the
  caveat must always surface when the flag is set, regardless of structural
  confidence.
- Do not treat PAE from an AlphaFold-**Monomer** prediction as evidence about
  inter-chain assembly; only an AlphaFold-**Multimer** prediction's PAE
  matrix carries genuine inter-chain positional information, and even then
  it should be validated against, not substituted for, the annotation-based
  complex flag.
