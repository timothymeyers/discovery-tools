---
name: sabio-rk-kinetics
description: |
  Life sciences / Enzymology — Parameterized SABIO-RK kinetic-law ingestion.
  Organism, genus, and EC-number query tiering (exact organism -> genus
  wildcard -> higher-taxon lineage -> any organism) driven by taxid ancestry
  rather than lineage-name string matching. Explicit outcome and failure_kind
  semantics that never conflate a confirmed-empty result with a transport,
  authorization, or parser failure. Sample-vs-exhaustive pagination with
  honest truncation reporting, and Retry-After-aware handling of the
  documented 60-requests/60-seconds rate limit. Nothing is scoped to an
  organism, project, or EC number — every identifier is a parameter. WHEN:
  "query SABIO-RK", "SABIO-RK kinetic law", "SABIO-RK Export API", "ingest
  kinetic parameters", "kcat", "Km", "enzyme kinetics for an organism", "EC
  number query tiers", "organism lineage fallback", "taxid ancestry",
  "SABIO-RK pagination", "SABIO-RK 429", "SABIO-RK rate limit".
metadata:
  version: "1"
  category: "Life sciences"
  subfield: "Enzymology / biochemical kinetics"
  secondary: "Physical sciences (chemical kinetics)"
---

# sabio-rk-kinetics — Parameterized SABIO-RK Kinetic-Law Ingestion

Reusable, project-agnostic adapter for querying the SABIO-RK Export API
(`/export-api/sabio/...`) for kinetic-law entries, generalized from a prior
ingestion pass that (1) built a stable-identifier-first, controlled-fallback
query strategy and (2) fixed a real production bug where a lineage-name
*string* match silently rejected genuinely matching organisms whose lineage
used a different (but current) NCBI taxon name than the one hardcoded in the
filter. Every organism/genus/EC-number/higher-taxon value here is a
parameter — this skill is not scoped to any single organism or project.

**Access-route status and general database-access procedure are owned by
the `scientific-database-access` hub skill** (`references/sources/sabio-rk.md`
there records the last-verified route status and rate-limit contract) — this
skill does not duplicate that; it assumes the Export API route has already
been confirmed reachable and focuses on the query/pagination/taxonomy/
outcome logic for actually pulling and post-filtering kinetic-law entries.

## When to use

- Query SABIO-RK for kinetic-law entries for a given EC number and target
  organism, with a documented, auditable fallback strategy when the exact
  organism has no direct SABIO-RK coverage.
- Debug or extend a lineage/taxonomy-based organism filter and want to avoid
  the "hardcoded lineage-name string never matches the API's actual current
  name" class of bug (see `references/taxonomy-ancestry-fallback.md`).
- Decide whether a zero-result query means "confirmed nothing exists" vs.
  "could not be confirmed due to a transport/auth/parse failure" — these
  must never be reported the same way (see
  `references/outcome-and-failure-semantics.md`).
- Choose between a bounded "sample" pagination pass and a full "exhaustive"
  pass, and need truncation always reported honestly rather than silently
  dropping pages (see `references/pagination-modes.md`).
- Handle SABIO-RK's documented 60-requests/60-second rate limit, including
  honoring a server-supplied `Retry-After` header on HTTP 429 (see
  `references/rate-limiting-and-retry-after.md`).
- Cheaply re-verify the Export API route is still live before relying on it,
  without risking the rate limit (`scripts/live_contract_probe.py`, opt-in).

## Query-tier strategy (parameterized, never a bare free-text search)

`scripts/query_tiers.py::build_query_tiers(ec_number, organism, genus,
fallback_taxon_name)` builds four ordered tiers from caller-supplied
parameters — see `references/query-tier-strategy.md` for the full rationale
and worked examples:

1. **Tier 1 — exact organism**: `ECNumber:<ec> AND Organism:"<organism>"`
2. **Tier 2 — genus wildcard**: `ECNumber:<ec> AND Organism:<genus>*`
3. **Tier 3 — lineage fallback**: `ECNumber:<ec>` (no organism clause —
   SABIO-RK's `Organism` field only indexes species-level names, not higher
   taxa), client-side post-filtered by taxid ancestry against a
   caller-supplied `fallback_taxid`.
4. **Tier 4 — any organism**: `ECNumber:<ec>`, no restriction at all —
   explicitly labeled as the broadest fallback, never conflated with an
   organism-matched observation. Reuses tier 3's already-fetched raw
   entries (same underlying query) instead of re-fetching.

`resolve_reaction_entries(...)` runs the tiers in order and stops at the
first with a nonzero result, recording every tier attempted (resolved or
not) plus an overall `status` of `resolved`, `confirmed_empty`, or
`query_incomplete_failure` — see
`references/outcome-and-failure-semantics.md` for why the last two must
never be conflated.

## Taxid-ancestry lineage classification (not string matching)

`scripts/ncbi_taxonomy_client.py::classify_higher_taxon_lineage(...)`
resolves an organism's NCBI taxid ancestry (via the NCBI Taxonomy E-utils
`efetch` endpoint, `scripts/ncbi_taxonomy_client.py::NcbiTaxonomyClient`) as
the *primary* check for tier-3 lineage matching, falling back to a
caller-supplied verified synonym set only when taxid resolution is
unavailable. An empty/missing lineage array is always classified
`unknown_empty_lineage` — never a match by omission. See
`references/taxonomy-ancestry-fallback.md` for the full worked example of
the historical bug this generalizes (a hardcoded lineage-name string
matching only one specific taxon name silently misses genuinely matching
organisms whose lineage array uses a different name for the same taxon —
e.g. a retired vs. current NCBI name for the same phylum).

## Pagination modes + honest truncation

`scripts/query_tiers.py::fetch_tier_entries(...)` supports `"sample"`
(bounded to one page) and `"exhaustive"` (follows all pages up to a safety
cap) pagination modes. Every tier summary always reports
`total_pages_available` and `truncated` regardless of mode — truncation is
never silent. See `references/pagination-modes.md`.

## Rate limiting + HTTP 429 / Retry-After

`scripts/sabio_rk_client.py::RateLimiter` enforces a sliding 60-requests/
60-second window (the Export API's documented, confirmed limit).
`SabioClient.get_json` retries once on HTTP 429, honoring a server-supplied
`Retry-After` header (RFC 9110: integer seconds or an HTTP-date) before
falling back to a computed backoff. See
`references/rate-limiting-and-retry-after.md`.

## Outcome / failure_kind semantics

Every HTTP call and every tier resolution reports an explicit `outcome`
(`"success"` / `"confirmed_empty"` / `"failure"`) and, on failure, a
`failure_kind` (`"transport"` / `"authorization"` / `"parser"`). These are
never conflated: a genuinely empty (HTTP 200, well-formed, zero matches)
result is reported differently from a query that could not be confirmed at
all. See `references/outcome-and-failure-semantics.md`.

## Opt-in live contract probe

`scripts/live_contract_probe.py` makes **one real, rate-limit-respecting**
HTTP request to confirm the Export API route is still reachable and still
returns the expected JSON envelope shape. It refuses to run without explicit
consent (`--i-understand-this-makes-a-live-request` or
`SABIO_RK_LIVE_PROBE=1`), caps itself at 5 requests per invocation, and
classifies the result using this repo's shared route-status vocabulary
(reused from the `scientific-database-access` hub skill, not duplicated).
**Never run this from an offline/CI test.**

## Files in this skill

- `scripts/sabio_rk_client.py` — `RateLimiter`, `HttpResponse`,
  `default_http_get`, `parse_retry_after_seconds`, `SabioClient` (rate-
  limited HTTP layer with 429/Retry-After retry, outcome/failure_kind
  classification, response caching + manifest).
- `scripts/ncbi_taxonomy_client.py` — `NcbiTaxonomyClient` (taxid ancestry
  resolution via NCBI E-utils `efetch`), `classify_higher_taxon_lineage`
  (parameterized taxid-ancestry-first lineage classification).
- `scripts/query_tiers.py` — `build_query_tiers`, `fetch_tier_entries`
  (paginated fetch with sample/exhaustive modes), `resolve_reaction_entries`
  (tier fallback + overall status).
- `scripts/ingest_kinetics.py` — CLI wiring: `--ec --organism --genus
  --fallback-taxid --fallback-taxon-name --verified-synonym
  --pagination-mode --cache-dir --output`. Always issues real HTTP calls
  when run directly.
- `scripts/live_contract_probe.py` — opt-in, rate-limit-safe live probe.
- `fixtures/` — small, redistributable synthetic (not live-captured)
  response fixtures: `tier1_exact_organism_hit.json`,
  `tier3_taxid_ancestry_mixed.json` (taxid-ancestry match + empty lineage +
  unrelated lineage in one page), `ncbi_efetch_ancestry.xml`,
  `pagination_page1_of2.json` / `pagination_page2_of2.json`,
  `outcome_429_retry_after.json`, `outcome_malformed_response.json`,
  `outcome_auth_failure.json`, `outcome_transport_failure.json`.
- `tests/` — offline pytest suite, no live network calls anywhere: rate
  limiter/retry-after/outcome semantics
  (`test_sabio_rk_client.py`), taxid-ancestry classification including a
  **mutation-sensitive test proving a historical hardcoded-string-match bug
  is not repeated** (`test_taxonomy_ancestry.py`), query-tier construction +
  pagination + full tier-resolution (`test_query_tiers_and_pagination.py`),
  CLI wiring (`test_ingest_kinetics_cli.py`), and live-probe consent-gating/
  route-status classification (`test_live_contract_probe.py`). Run with
  `python3 -m pytest tests/ -q` from this skill's directory.

## Generalization note

No EC number, organism, genus, taxid, or fallback taxon name is hardcoded
anywhere in `scripts/`. When adapting this skill to a new organism or EC
number, pass your own values via the CLI flags or function parameters —
never import another project's crosswalk values as if they were general
defaults. The fixture organisms/taxa used in tests (*Escherichia coli*,
*Pseudomonas putida*, Pseudomonadota) are illustrative placeholders only.
