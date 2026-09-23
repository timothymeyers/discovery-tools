# MetaNetX

**Last verified:** 2026-09-22
**Evidence:** `docs/database-evidence/source-priority-matrix.md`,
`docs/database-evidence/raw/metanetx_reac_prop_bulk_mirror_head.tsv`

## Routes

| Route | Status | Detail |
|---|---|---|
| Bulk TSV download (e.g. `reac_prop` tables) | `reachable_public` | Confirmed 200 OK, no auth, MNXref version identified in the file header. **Recommended route** for cross-reference reconciliation work. |
| Direct single-entry REST path (`/reac_info/MNXR...`) | `dead_unconfirmed` | A guessed/direct entry ID 404'd. Needs an exact ID sourced from the bulk file (or a documented `/chem/`, `/reac/`-prefixed path) rather than a guessed identifier — this looks like a wrong-input result, not a confirmed-dead endpoint. |

## Key facts worth preserving

- License: free, CC-BY 4.0. Explicit version + date in the bulk file header
  — use for pinning.
- Tracks source-database provenance per mapped ID — useful for keeping
  cross-reference reconciliation auditable back to its origin database.
- No condition/kinetic data — this is a cross-database ID reconciliation
  layer only.

## Do not generalize

- A 404 on a guessed direct-entry URL is not proof the REST path is
  discontinued — resolve exact IDs via the bulk file first, then retry the
  single-entry path if still needed.
