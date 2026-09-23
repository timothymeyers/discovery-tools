# BRENDA

**Last verified:** 2026-09-22
**Evidence:** `docs/database-evidence/dx29-brenda-ingestion-methods.md`,
`docs/database-evidence/raw/dx29/brenda_bulk_release_manifest.json`,
`docs/database-evidence/raw/brenda_soap_wsdl.xml`,
`docs/database-evidence/raw/brenda_web_ec1.2.1.13_gapdh.html`

## Routes

| Route | Status | Detail |
|---|---|---|
| SOAP API (`soap.php`) | `gated_credentialed` | WSDL is publicly served (200 OK), but every operation requires a registered account's email + SHA-256 password hash per call. Contract shape confirmed by reading the WSDL; no live call was authorized/made without credentials. |
| Bulk JSON release (`download.php`) | `reachable_documented_auth` | A `POST /download.php` with `accept-license=1` and the desired file selector returns the release directly (HTTP 200, `Content-Disposition: attachment`) — **no account/email/password required**, only a click-through license-acceptance step. This is the recommended route for organism/EC-linked data extraction. |
| Interactive website (`brenda-enzymes.org/enzyme.php?ecno=...`) | `reachable_public` | Publicly browsable HTML, but not a stable machine-readable contract — scraping only, not a recipe to build a durable adapter on. |
| SPARQL endpoint (`sparql.dsmz.de/api/brenda`) | `reachable_public` | Live, registration-free SPARQL 1.1 endpoint (confirmed real `application/sparql-results+json` responses). Ontology/predicates for organism/taxon queries were not fully resolved — treat as reachable but not yet a validated recipe for organism-scoped queries. |

## Key facts worth preserving

- The bulk JSON release is licensed under CC-BY 4.0; validate the downloaded
  file against BRENDA's own published JSON Schema before extracting anything.
- Join organism-specific data at the **protein/annotation level**
  (`proteins` cross-reference array), never assume every field listed under
  an EC number applies to every organism under that EC.
- The `-999 {more}` value convention is a comment/sentinel, not a measured
  negative parameter — parse accordingly.
- SOAP being credential-gated is **not** evidence that BRENDA overall is
  inaccessible — the licensed bulk route is a fully separate, working path.

## Do not generalize

- "No numeric kinetic parameters for a given organism/EC in one release" is a
  literature-coverage gap, not proof BRENDA lacks that data category, and not
  an access failure.
