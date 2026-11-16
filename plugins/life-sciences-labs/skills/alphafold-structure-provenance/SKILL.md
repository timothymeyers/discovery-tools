---
name: alphafold-structure-provenance
description: |
  Life sciences / Structural biology — Taxon-scoped UniProt accession lookup
  plus version-aware AlphaFold DB structure retrieval. Keeps pLDDT/PAE quality
  triage strictly separate from oligomer and complex assembly flags, and
  requires recording exactly which AlphaFold structure version any derived
  quantity (such as a HullRad diffusion coefficient) was computed from.
  Encodes AlphaFold DB version-retention behaviour: latestVersion is
  authoritative for what can be downloaded today, while allVersions is a
  historical list only — older version-numbered structure, PAE, and confidence
  files typically 404 once an entry moves to a newer version. WHEN: "look up
  UniProt accessions for an organism", "fetch AlphaFold structures",
  "AlphaFold API", "pLDDT triage", "PAE triage", "structure quality
  assessment", "AlphaFold model version", "latestVersion vs allVersions",
  "oligomer flag", "structure provenance", "AlphaFold DB 404".
metadata:
  version: "1"
  category: "Life sciences"
  subfield: "Structural and computational biology"
  secondary: "Biophysics (diffusion values computed from structures)"
---

# alphafold-structure-provenance — Taxon-Scoped UniProt Lookup + Version-Aware AlphaFold Retrieval

Hub skill for two chained steps that recur across structure-quality and
hydrodynamic-property (e.g. HullRad) workflows:

1. **Taxon-scoped UniProt accession lookup** — resolve which UniProt
   accessions belong to a given organism/taxon before doing anything with
   AlphaFold.
2. **Version-aware AlphaFold DB structure retrieval** — fetch the *correct,
   currently-downloadable* structure/confidence/PAE files for those
   accessions, and record exactly which AlphaFold model version was used for
   every derived downstream quantity.

It also documents two triage/flagging steps that must stay independent
of each other and of the version-provenance step: **pLDDT/PAE quality
triage** and **oligomer/complex assembly flags**.

## When to use

Trigger this skill when the user asks to:

- "Look up all UniProt accessions for organism/taxon X" or otherwise scope a
  protein list to a specific organism before structural analysis.
- "Fetch AlphaFold structures for these accessions", "download AlphaFold
  models", or "get pLDDT/PAE for these proteins".
- Debug an AlphaFold DB `404` on a structure/confidence/PAE file that used to
  work, or reconcile a mismatch between a locally-recorded model version and
  what AlphaFold DB currently serves.
- Assess structure quality (pLDDT bands, low-confidence segments, PAE) for a
  candidate list.
- Decide whether a monomer AlphaFold model is trustworthy for downstream use
  when the protein is annotated as part of a multi-subunit complex.
- Record or audit *which AlphaFold structure version* a downstream derived
  quantity (HullRad Dt/Rg/Dmax, or any other geometry-derived value) was
  computed from, so results stay traceable and reproducible.

## Step 1 — Taxon-scoped UniProt accession lookup

Resolve the accession list for a specific organism/taxon via the UniProt REST
API (`https://rest.uniprot.org/uniprotkb/search`) using a `taxonomy_id:<NCBI
taxon ID>` (or `organism_id:`) query filter, rather than pulling accessions
from an unscoped or mixed-organism source. Always record:

- the exact taxon ID / query used,
- the UniProt release / retrieval date,
- reviewed (Swiss-Prot) vs. unreviewed (TrEMBL) status per accession,
- the total accession count returned, for later reconciliation.

See `references/uniprot_taxon_lookup.md` and
`scripts/uniprot_taxon_lookup.py` (adapt the taxon ID and any organism-specific
filters for your own project — do not reuse another project's hard-coded
taxon ID without checking it matches your organism).

## Step 2 — Version-aware AlphaFold DB structure retrieval

**Core rule: `latestVersion` is authoritative for what can be downloaded
today. `allVersions` is historical metadata only, not a download menu.**

Calling `GET https://alphafold.ebi.ac.uk/api/prediction/{accession}` returns,
among other fields, `latestVersion` (an integer, e.g. `6`) and `allVersions`
(a list of every version number that entry has ever had, e.g. `[2,3,4,5,6]`).
It is tempting to assume any number in `allVersions` names a file you can
still fetch — **this is false**. AlphaFold DB generally retains only the
current `latestVersion`'s files on its file server
(`https://alphafold.ebi.ac.uk/files/AF-{accession}-F{n}-{artifact}_v{N}.*`);
requesting an older version number from `allVersions` that is not the current
`latestVersion` commonly returns HTTP `404`, while the current
`latestVersion` file returns HTTP `200`. A worked example of this exact
behavior (older version numbers 404, current version 200, confirmed by direct
HTTP probe) is recorded in `references/version_provenance.md`, distilled from
a prior structure-quality assessment; treat it as one illustrative case, not
a claim that a specific version number is universally the "current" one —
always re-check `latestVersion` live for your own accessions.

**Practical consequence:** before fetching any structure/confidence/PAE file,
call the prediction-metadata endpoint first, read `latestVersion`, and build
file URLs using that version number — never an older number carried over
from a prior run's cached provenance record. If a downstream artifact (e.g. a
HullRad Dt) was computed from an older version, do not assume you can
re-fetch the identical file: check `latestVersion` again and, if it has
advanced, record that the source version is no longer available rather than
silently re-fetching a different structure and treating it as identical.

Use `scripts/fetch_alphafold_structure.py` for the fetch-with-version-check
pattern; it always resolves `latestVersion` from the API before downloading
files and caches the raw API response for provenance.

## Step 3 — pLDDT/PAE quality triage (keep separate from Step 4)

pLDDT (a per-residue confidence score, 0–100) and PAE (predicted aligned
error, a pairwise positional-uncertainty matrix) describe confidence in the
**modeled chain's local backbone geometry only**. Triage logic based on
pLDDT/PAE (e.g. "≥80% confident-or-better → usable as-is", "<30% → exclude")
must be implemented as its own independent decision axis, using
configurable, documented thresholds — see `references/plddt_pae_triage.md`
for the standard AlphaFold confidence bands (>90 very high, 70–90 confident,
50–70 low, ≤50 very low; Jumper et al. 2021 *Nature*) and example threshold
bands. Never encode oligomer/complex logic inside this function or let a
complex annotation change the pLDDT band a residue falls into — that
belongs entirely to Step 4.

## Step 4 — Oligomer / complex flags (keep separate from Step 3)

Whether a protein's true biological unit is a monomer, homo-oligomer, or
hetero-complex is an **entirely separate question** from monomer pLDDT/PAE
quality, and must be tracked as an independent flag (e.g. from a UniProt
`cc_subunit` / "subunit structure" annotation, or another authoritative
source). A high monomer pLDDT does **not** validate an oligomeric assembly,
and a low monomer pLDDT does not indict it either. See
`references/oligomer_complex_flags.md` for the recommended pattern: cap or
annotate (never silently override) a pLDDT-based decision when the
complex/oligomer flag is set, and always keep the two reason strings
separate so a reviewer can tell which axis drove which part of the outcome.

## Step 5 — Recording structure-version provenance for derived quantities

Any quantity computed from an AlphaFold structure (HullRad `Dt`, `Rg`,
`Dmax`, or any other geometry-derived value) must be stored alongside the
**exact AlphaFold model version** it was computed from (e.g. `model_v4`,
`model_v6`), not just the accession -- **plus** a content checksum of the
exact structure file used (`source_structure_checksum`) and the name and
version of the method/tool that computed the quantity
(`computation_method` / `computation_method_version`). The checksum and
method-version fields are mandatory, not optional: the structure version
number alone tells you which AlphaFold release was used, but not whether the
locally-cached file bytes are intact, and not which tool (or tool version)
turned those bytes into the derived number. When re-verifying or re-deriving
such a quantity later:

1. Fetch current `latestVersion` for the accession.
2. Compare it to the recorded source version.
3. If they match, note "exact version still available."
4. If they differ, do **not** silently treat a re-fetch of the new
   `latestVersion` as equivalent — record an explicit version-mismatch /
   provenance-caveat string stating the derived quantity's original version
   may no longer be independently re-verifiable, and that newly-fetched
   pLDDT/PAE evidence describes the *current* model, not necessarily the
   exact geometry the original quantity was computed from.

This keeps every derived result traceable to a specific AlphaFold DB version
even as the database moves forward, and prevents version drift from being
silently absorbed into "the same" result. See
`references/version_provenance.md` for the full pattern, the mandatory
`source_structure_checksum` / `computation_method` / `computation_method_version`
fields, and an example provenance-column layout.

## Files in this skill

- `references/uniprot_taxon_lookup.md` — UniProt REST query patterns for
  taxon-scoped accession lookup, plus fields to record for provenance.
- `references/version_provenance.md` — `latestVersion` vs. `allVersions`
  semantics, the 404-vs-200 worked example, and a recommended
  provenance-column layout for derived quantities.
- `references/plddt_pae_triage.md` — standard pLDDT confidence bands, PAE
  usage notes, and example configurable triage thresholds.
- `references/oligomer_complex_flags.md` — pattern for tracking
  oligomer/complex assembly risk as an axis independent of pLDDT/PAE triage.
- `scripts/uniprot_taxon_lookup.py` — query the UniProt REST API for all
  accessions under a given taxon ID; writes a TSV with reviewed status and
  retrieval provenance.
- `scripts/fetch_alphafold_structure.py` — fetch AlphaFold DB prediction
  metadata for an accession, resolve `latestVersion`, and download the
  current structure/confidence/PAE files using that version number, caching
  the raw API response and recording the version used.
- `tests/test_fetch_alphafold_structure.py` — offline, mocked tests (no
  network access) proving `latestVersion`, not `allVersions`, drives the
  downloaded filename, and covering malformed/missing `latestVersion` and
  artifact-download error paths. Run with `python -m unittest discover -s
  tests -p "test_*.py" -v` from this skill's directory.

## Generalization note

The version-retention behavior and thresholds described here are general
AlphaFold DB / AlphaFold-Monomer properties, not specific to any one
organism or project. When adapting this skill to a new dataset, re-derive
your own thresholds and taxon IDs rather than importing another project's
organism-specific candidate list or conclusions as if they were general
rules.
