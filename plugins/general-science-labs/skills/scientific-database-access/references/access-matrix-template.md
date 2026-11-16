# Access-matrix template

Fill in one row per **(source, route)** pair. A source almost always has more
than one route — never collapse them into a single "is X accessible?" verdict.

## Per-route record

```yaml
source: <human name, e.g. "SABIO-RK">
route: <website | documented_api | bulk_export | mirror | single_object_lookup>
route_detail: <exact base URL / endpoint path / file path — not the whole domain>
status: <one of references/route-status-vocabulary.md>
request_made:
  method: <GET/POST/etc.>
  url_or_path: <redact query params that contain secrets>
  auth_used: <none | documented-license-click | public-api-key | NOT AVAILABLE (credentialed, untested)>
response_evidence:
  http_status: <int>
  content_type: <string>
  body_shape: <e.g. "JSON envelope with meta/data", "HTML SPA shell", "SBML/XML">
  body_sample_or_hash: <short excerpt or checksum — never the full secret-bearing payload>
  headers_of_note: <rate-limit headers, Cache-Control, cookies (name only, not value)>
retrieved_at: <ISO 8601 date/time>
scope: <exactly what this claim covers — e.g. "EC 1.2.1.13, Tier-1 query only", not "the whole database">
license_permission:
  reachable: <true/false — purely technical>
  permitted_use: <what the terms actually allow: personal lookup / bulk redistribution / commercial — separate from reachable>
  terms_source: <link/citation to the terms actually read>
supersedes: <pointer to the prior claim this replaces, or "none">
evidence_file: <path under docs/database-evidence/raw/... or equivalent, if retained>
```

## Worked example (abbreviated, from real project evidence)

```yaml
source: SABIO-RK
route: documented_api
route_detail: https://sabiork.h-its.org/export-api/sabio/kinlaw-entry/json
status: reachable_public
request_made:
  method: GET
  url_or_path: /export-api/sabio/kinlaw-entry/json?q=ECNumber:1.2.1.13
  auth_used: none
response_evidence:
  http_status: 200
  content_type: application/json
  body_shape: "JSON envelope with meta.total_count/total_pages + data[]"
  body_sample_or_hash: "see docs/database-evidence/raw/dx28/<key>.json"
  headers_of_note: "X-Total-Count, Link rel=next/prev/first/last"
retrieved_at: 2026-09-22
scope: "3 mapped EC numbers, Tiers 1-4 organism fallback, page cap 1/tier"
license_permission:
  reachable: true
  permitted_use: "unclear — no license terms retrieved this pass; treat as reachable-only until confirmed"
  terms_source: "not yet located — follow-up"
supersedes: "dx24 finding that legacy /sabioRestWebServices/... is non-functional (still true; different route)"
evidence_file: docs/database-evidence/raw/dx28/manifest.json
```

Notice the earlier "SABIO-RK has no usable API" claim is **not overwritten**
— it remains true for the *legacy* route. The new record supersedes it only
for the *documented Export API* route, and says so explicitly.

## Anti-patterns to avoid when filling this out

- Writing "SABIO-RK: unavailable" as a single verdict instead of one row per
  route.
- Recording `permitted_use` as identical to `reachable` — a route can be
  reachable with no license research done at all; say so honestly rather
  than assuming permission follows from reachability.
- Omitting `retrieved_at` / `supersedes` — this is what lets a later task
  distinguish a fresh finding from stale memory.
