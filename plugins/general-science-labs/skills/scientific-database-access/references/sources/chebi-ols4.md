# ChEBI (via EBI OLS4)

**Last verified:** 2026-09-22
**Evidence:** `docs/database-evidence/source-priority-matrix.md`,
`docs/database-evidence/raw/chebi_ols_probe_CHEBI17111.json`

## Routes

| Route | Status | Detail |
|---|---|---|
| EBI OLS4 API | `reachable_public` | Confirmed 200 OK, no auth, real JSON for a known-valid ChEBI ID. **Recommended route.** |
| Legacy ChEBI SOAP/REST `getCompleteEntity` | `dead_unconfirmed` | Returned HTTP 500 in the retained probe. Should be avoided in favor of OLS4 rather than retried as-is. |

## Key facts worth preserving

- License: free, CC-BY 4.0.
- OLS versions ChEBI releases explicitly — record the OLS/ChEBI release
  alongside any extracted compound data.
- Provides compound identity/structure/ontology (parent-child relations),
  not organism or kinetic data.

## Do not generalize

- The legacy SOAP/REST 500 was observed on one specific operation
  (`getCompleteEntity`); this is recorded as `dead_unconfirmed`, not
  `dead_superseded`, because no exhaustive check of every legacy operation
  was made — OLS4 is simply the confirmed-working, recommended path.
