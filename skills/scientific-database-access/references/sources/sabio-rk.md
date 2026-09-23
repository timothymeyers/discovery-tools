# SABIO-RK

**Last verified:** 2026-09-22
**Evidence:** `docs/database-evidence/dx28-sabio-rk-ingestion-methods.md`,
`docs/database-evidence/raw/dx28/openapi-export-api.json`,
`docs/database-evidence/raw/sabiork_probe_spa_shell_NONFUNCTIONAL.html`

## Routes

| Route | Status | Detail |
|---|---|---|
| Legacy REST (`/sabioRestWebServices/...`, `kineticLawEntry.jsp`) | `dead_superseded` | Redirects to the site's SPA shell (`/ui/404`); returns HTML, not SBML/XML. |
| Documented Export API (`/export-api/sabio/kinlaw-entry/json`) | `reachable_public` | Found via the SPA's own JS bundle referencing a machine-readable OpenAPI spec at `/openapi/export-api.json`. No authentication. Confirmed 200 OK with real kinetic-law JSON, pagination headers (`X-Total-Count`, `Link` rel=next/prev/first/last). |
| Interactive website | Not separately probed this pass (superseded by API route). | — |

## Key facts worth preserving

- Documented rate limit: 60 requests / 60-second window per client IP,
  shared across all `/export-api/` routes.
- Query with stable identifiers (EC number), never bare free-text species
  search. Organism filtering only indexes species-level names, not higher
  taxa — a lineage/synonym-aware fallback is needed to catch genus/family
  matches (do not rely on exact string match against a single lineage name;
  see dx22 §3 "Taxonomy-name drift" finding).
- No global dataset-release/version identifier is exposed by this API —
  record `source_version` as unavailable/unknown rather than guessing one.
- License/redistribution terms for the Export API were **not** confirmed in
  the retained evidence — treat as reachable-only until terms are read.

## Do not generalize

- "No cyanobacterial entries were found for these 3 EC numbers" is a
  query-specific/sampling finding, not proof of SABIO-RK's overall
  cyanobacteria coverage — some retained entries had unrecognized lineage
  strings that a stricter taxonomy match missed.
