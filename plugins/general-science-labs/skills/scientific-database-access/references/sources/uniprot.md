# UniProt

**Last verified:** 2026-09-22
**Evidence:** `docs/database-evidence/source-priority-matrix.md`,
`docs/database-evidence/raw/uniprot_gapdh_pmarinus_med4.json`,
`docs/database-evidence/raw/uniprot_pgk_pmarinus_med4.json`,
`docs/database-evidence/raw/uniprot_rbcL_pmarinus_med4.json`

## Routes

| Route | Status | Detail |
|---|---|---|
| REST API (`rest.uniprot.org`) | `reachable_public` | Fully public, no auth, JSON. All test queries succeeded structurally in the retained evidence. **Recommended route.** |

## Key facts worth preserving

- License: free, CC-BY 4.0. Release-versioned (UniProt release train) —
  record the release version alongside extracted entries.
- Excellent organism/strain-level coverage in general, but coverage is
  entry-dependent: a zero-hit query result for one specific gene/organism
  combination is a genuine, real coverage gap to report as such — it is
  **not** an access failure, since the API responded correctly and simply
  had no matching record.
- Strong provenance: extremely well cross-referenced and versioned entries.

## Do not generalize

- A zero-hit search for a narrowly-scoped query (specific gene x exact
  strain) does not mean UniProt lacks data for the organism/protein family in
  general — broaden the query deliberately and record both results before
  concluding a coverage gap.
