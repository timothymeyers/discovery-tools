# AlphaFold DB

**Last verified:** 2026-09-22 (indirect — see caveat below)
**Evidence:** referenced via DX-12 structure-quality methods material,
outside the DX-22 access-review subtree; not independently re-probed for
this skill.

## Caveat

Mission Control review material (DX-22) explicitly notes that promising
AlphaFold-related material was surfaced during a related review but falls
**outside** the subtree that produced the verified access findings for the
other seven sources in this reference set. Treat the rows below as a
starting checklist to verify, not as confirmed access findings equivalent
to the other source cards.

## Routes to verify before use

| Route | Status | Detail |
|---|---|---|
| Public per-entry API/download (`alphafold.ebi.ac.uk/api/prediction/<uniprot_accession>` and per-entry structure files) | `unknown_untested` | Documented publicly by EBI; re-probe with a known-valid UniProt accession before relying on it, and record the result using the access-matrix template. |
| Bulk/mass structure download sets (per-organism or full-database tars) | `unknown_untested` | EBI publishes bulk download sets; confirm current terms and current URLs before assuming they are unchanged. |
| Version-aware structure retrieval (matching a specific AlphaFold model version to a UniProt release) | `unknown_untested` | Only pursue after confirming a documented version-pinning mechanism; do not assume the latest served structure matches an old cached UniProt release without checking version metadata on both sides. |

## Key facts worth preserving

- License: AlphaFold DB structures are provided under CC-BY 4.0 for the EBI
  bulk sets — reconfirm current terms before redistribution, as with any
  other source.
- Structure predictions are versioned; do not treat "a structure exists" as
  proof it matches the specific UniProt/organism release version in use
  elsewhere in a project without checking.

## Do not generalize

- Do not treat this card as equivalent in verification strength to the other
  seven source cards in this reference set — it is a placeholder checklist
  pending an actual access probe.
