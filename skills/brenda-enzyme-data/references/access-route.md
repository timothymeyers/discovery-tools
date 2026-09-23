# BRENDA Access Route Summary (for this skill)

Full route-status detail, evidence pointers, and "do not generalize" caveats
live in the `scientific-database-access` hub skill's
`references/sources/brenda.md` — read that first. This document only
summarizes what matters for *using this skill*.

## Route you need for this skill

**Bulk JSON release (`download.php`)** — `reachable_documented_auth`. A
`POST /download.php` with `accept-license=1` and a file selector
(`dlfile=dl-json`) returns the release directly (HTTP 200,
`Content-Disposition: attachment`). **No account, email, password, or
CAPTCHA is required** — only a real, one-time, human click-through license
acceptance on BRENDA's own page. This is the route this skill's scripts
assume you have already exercised, on your own, before running them.

## Routes this skill does NOT use

- **SOAP API (`soap.php`)** — `gated_credentialed`. Every operation requires
  a registered account's email + SHA-256 password hash per call. This
  skill does not read `BRENDA_EMAIL`/`BRENDA_PASSWORD_SHA256` or attempt
  any SOAP call; `scripts/live_contract_probe.py` only checks the WSDL is
  still publicly served (contract-shape reachability, not a live operation
  call).
- **SPARQL endpoint (`sparql.dsmz.de/api/brenda`)** — `reachable_public`,
  registration-free, but its organism/taxon predicate ontology has not been
  fully validated as a recipe for organism-scoped queries. This skill's
  probe checks only that the endpoint answers a generic `SELECT` — it is
  not a validated extraction path here.
- **Interactive website scraping** — not a stable machine-readable
  contract; out of scope for this skill entirely.

## Handling your own downloaded full dump

The full bulk JSON release is large (hundreds of MB uncompressed) and is
**licensed data you obtained yourself** — never commit it to a
version-controlled repository. If you're using this skill inside a git
repo, add an ignore rule before downloading, e.g.:

```gitignore
# Full BRENDA bulk dump: obtained under your own license acceptance;
# never committed.
/local-cache/brenda/
```

Only pass the small, redistributable *output* of `extract_subset.py` (a
trimmed, organism/EC-scoped extract, typically tens to a few hundred KB) to
version control or downstream tooling — never the full dump itself.

## License terms (read them yourself)

BRENDA's data is licensed under **Creative Commons Attribution 4.0 (CC BY
4.0)**. BRENDA's license page has also carried a note about potential
benefit-sharing obligations under the UN CBD's Digital Sequence Information
(DSI) multilateral mechanism for commercial users — this skill does not
interpret or apply that clause for you; read BRENDA's current license page
yourself (`https://www.brenda-enzymes.org/license.php`) before any
commercial use of extracted data.
