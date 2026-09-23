# AlphaFold DB version provenance: `latestVersion` vs. `allVersions`

## The core rule

`GET https://alphafold.ebi.ac.uk/api/prediction/{accession}` returns (among
other fields) an entry with:

- `latestVersion` — an integer identifying the model version AlphaFold DB
  currently serves for this accession. **This is the only version number
  guaranteed to be downloadable right now.**
- `allVersions` — a list of every version number this entry has ever had
  (e.g. `[2, 3, 4, 5, 6]`). **This is historical metadata only.** It records
  that those versions existed at some point; it does **not** mean their
  structure/confidence/PAE files are still hosted or downloadable.

Do not write code or reasoning that treats membership in `allVersions` as
sufficient justification to construct a download URL for that version. The
only version number safe to build a file URL from is the current
`latestVersion`, re-checked at fetch time.

## Worked example (illustrative, not a general organism claim)

A prior structure-quality assessment (see
`docs/dx12_structure_quality_methods.md` in the workspace this skill was
authored from, if present) directly probed the AlphaFold DB file server for a
protein whose `allVersions` list included versions 2 through 6, with
`latestVersion = 6`. Requesting the older, non-latest version-numbered
structure files (e.g. `-model_v4.cif`, `-model_v5.cif`) returned HTTP `404`
for every accession tested, while the current `-model_v6.cif` returned HTTP
`200`. This is presented here as **one illustrative confirmation of the
general `latestVersion`-vs-`allVersions` behavior**, not as a universal
statement that version 6 (or any other specific number) is "the" current
version for all AlphaFold entries — always re-resolve `latestVersion` live
for the accession you are working with; it changes over time as AlphaFold DB
reprocesses entries.

File URL pattern (build only with the freshly-resolved `latestVersion`,
substitute `N`):

```
https://alphafold.ebi.ac.uk/files/AF-{accession}-F1-model_v{N}.cif
https://alphafold.ebi.ac.uk/files/AF-{accession}-F1-confidence_v{N}.json
https://alphafold.ebi.ac.uk/files/AF-{accession}-F1-predicted_aligned_error_v{N}.json
```

## Recommended provenance-column layout for derived quantities

Any quantity derived from an AlphaFold structure (a HullRad `Dt`, `Rg`,
`Dmax`, or any other geometry-derived value) should carry these columns
alongside the value itself:

| Column | Meaning |
|---|---|
| `source_structure_version` | Exact AlphaFold model version (e.g. `4`, `6`) the derived quantity was computed from |
| `source_structure_path_or_url` | The specific file path/URL used at computation time |
| `source_structure_checksum` | A content checksum (e.g. `sha256:<hex>`) of the exact structure file bytes used, computed at the time of the run. This is what makes the row verifiable even if the file path/URL later changes or the file is no longer hosted: recompute the checksum on any locally-cached copy and compare, rather than trusting the path/version label alone. |
| `computation_method` | Name of the method/tool that produced the derived quantity (e.g. `HullRad`), independent of the AlphaFold structure version |
| `computation_method_version` | Version of that method/tool (e.g. HullRad version string, or a script/commit identifier), so a changed result can be attributed to a structure-version change, a method-version change, or both |
| `af_latest_version_at_check` | `latestVersion` as of the most recent re-check (may differ from `source_structure_version`) |
| `model_version_exact_match` | `"exact version still available"` if the two match; otherwise an explicit `"NO -- ..."` string explaining the mismatch |
| `version_provenance_caveat` | Free-text note: if versions differ, state that the original file may no longer be independently re-fetchable, and that any newly-fetched pLDDT/PAE describes the *current* model, not proven-identical geometry to the original |

**`source_structure_checksum` and `computation_method`/`computation_method_version` are mandatory, not optional**, for any row recording a derived quantity. A version number alone identifies *which AlphaFold release* was used, but not the *exact bytes* actually consumed (a locally-cached file could be corrupted, truncated, or accidentally overwritten) and not *which computation produced the number* or with what tool version. Recording all four fields together is what lets a reviewer independently re-verify that a specific derived value came from a specific structure file, computed by a specific, version-pinned method.

Example row (illustrative values only):

| source_structure_version | source_structure_path_or_url | source_structure_checksum | computation_method | computation_method_version | af_latest_version_at_check | model_version_exact_match | version_provenance_caveat |
|---|---|---|---|---|---|---|---|
| 6 | `AF-P00000-F1-model_v6.cif` | `sha256:9f1c2a...` | HullRad | 1.0 | 6 | exact version still available | (none) |
| 4 | `AF-P00001-F1-model_v4.cif` | `sha256:3bd7e0...` | HullRad | 1.0 | 6 | NO -- prior source used version 4, AlphaFold DB now serves latestVersion 6 | original v4 file no longer independently re-fetchable (404); newly-fetched v6 pLDDT/PAE describes the current model only |

## Why this matters

Without this bookkeeping, a re-run of a pipeline months later can silently
substitute a newer AlphaFold structure for an older one a downstream value
was actually computed from, without anyone noticing the swap. Surfacing the
version-match status per row (even when it does not change the decision
outcome) keeps every result honestly traceable to the structure that
actually produced it.
