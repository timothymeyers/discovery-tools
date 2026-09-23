# Source recipes and access gating

Only the recipes below have been verified end-to-end against a live/bulk
source in this project (DX-24/DX-27). Anything else in the schema's
`identifiers.<namespace>` list that is not covered here must be recorded
as `unavailable_source`/`unknown` — never guessed by analogy.

## Rhea (verified)

- Bulk mirror (Expasy FTP) is the grounded recipe: `rhea-reactions.txt`
  (ChEBI-only equations, all 4 directional IDs), `rhea-directions.tsv`
  (UN/LR/RL/BI groupings), `rhea2xrefs.tsv` (EC/KEGG/MetaCyc/EcoCyc
  cross-references), `rhea2ec.tsv`, `rhea2kegg_reaction.tsv`,
  `rhea2metacyc.tsv`, `rhea2ecocyc.tsv`.
- Record the release number/date exactly as embedded in the mirror
  (e.g. `release 142 (2026-09-02)`) as `source_version` — never leave it
  as "latest".
- **Read the retrieved equation text to resolve direction.** Never derive
  direction from the LR/RL suffix alone — see
  `references/ambiguity-taxonomy.md#direction`.

## ChEBI via EBI OLS4 REST API (verified)

- Per-term lookup gives `name`, `formula`, net `charge`, and
  `is_deprotonated_form_of`/`is_protonated_form_of` relations to sibling
  charge-state terms.
- Record the ChEBI ontology version loaded at query time (e.g.
  `"ChEBI ontology version 255 (loaded 2026-09-22)"`).
- This is the **independent** source for `formula`/`charge` used by the
  mass/charge balance check — never derive these numbers from the
  reaction equation itself, or the check becomes a tautology against its
  own inputs.

## MetaNetX (verified, exact-hit only)

- Bulk `reac_xref.tsv` gives exact-hit MNXR reconciled-reaction IDs.
  `chem_xref.tsv`/`chem_prop.tsv` compound-level (MNXM) reconciliation via
  exact charge-state ChEBI ID has **not** been verified as a reliable
  recipe yet in this project — record `unknown`, not a guessed MNXM ID,
  until that path is independently confirmed.
- Record `source_version` as the MNXref release tag (e.g. `"MNXref v4.5
  (2025-08-13)"`).

## UniProt (verified, live REST API)

- Organism-scoped queries (taxid + gene name/EC) return accessions with
  review status (Swiss-Prot reviewed vs. TrEMBL unreviewed) and EC
  annotations.
- **A narrow query can miss real isoenzyme paralogs.** DX-27 found GAPDH
  paralogs gap1/gap2 only after broadening the organism-scoped query past
  DX-24's original narrower one. Prefer a broader organism+EC-class query
  over a single gene-name query when isoenzyme ambiguity is plausible.
- Record the UniProt release train (e.g. `"UniProt release 2026_03
  (2026-09-02)"`).

## KEGG (verified only as a cross-reference inside Rhea, not queried
directly)

- KEGG COMPOUND/REACTION IDs surfaced via `rhea2kegg_reaction.tsv` and
  ChEBI's own KEGG cross-reference are legitimate to record with
  `method: direct_xref_file` and a note identifying the bridging source.
- Querying KEGG's own API/bulk files directly for anything beyond casual
  browsing requires a commercial license this project has not procured —
  do not silently assume unlicensed programmatic KEGG access is fine
  because a cross-referenced ID happened to be visible via Rhea/ChEBI.

## Sources confirmed GATED / not queryable in this project

Record these as `unavailable_source` (distinct from `unknown`) and cite
the gating reason, not a fabricated value:

- **CyanoCyc** (organism-specific PGDB tier) — subscription required.
- **BRENDA** (SOAP API) — registered-credential access required; see the
  `brenda-enzyme-data` skill for the licensed-bulk-release alternative
  recipe, which is a different access path from the live SOAP API.
- **SABIO-RK** — the legacy REST endpoint referenced in older project
  notes was non-functional as of the DX-24/DX-28 verification pass; see
  the `sabio-rk-kinetics` skill for the current API status.

An `unavailable_source` record is not permanent fact — it reflects the
state confirmed at `retrieved_at`. Re-verify before assuming a gate is
still in place on a later task.
